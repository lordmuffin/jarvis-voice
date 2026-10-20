/**
 * Pull the audio track out of an MP4/MOV video in the browser, so a multi-gigabyte video
 * uploads as tens of megabytes. The server keeps only the audio anyway.
 *
 * No decoding: the track's compressed frames are copied out as they are. AAC frames get ADTS
 * headers (a `.aac` stream); MP3 frames are self-framing and are simply concatenated. Only the
 * container's index and the audio bytes are read (`File.slice`), never the whole file.
 *
 * Both layouts are read: a regular MP4 (one index in `moov`) and a fragmented one (each `moof`
 * indexes the `mdat` after it), as OBS and some screen recorders write.
 *
 * Returns null when the file is not something this can copy (another container, an audio
 * codec other than AAC/MP3), logging why; the caller then uploads the file as it is.
 */

interface Box {
  type: string;
  /** Offset of the box's payload, after its header. */
  start: number;
  end: number;
}

interface AudioTrack {
  id: number;
  codec: "aac" | "mp3";
  /** AAC only: the 7-byte ADTS header's fixed part (profile, rate, channels). */
  adts?: { profile: number; freq: number; channels: number };
  sizes: number[];
  chunkOffsets: number[];
  /** stsc: [first chunk (1-based), samples per chunk]. */
  samplesPerChunk: [number, number][];
  /** Its moov has mvex: more samples follow in moof fragments. */
  fragmented: boolean;
  /** trex: the sample size fragments use when they don't say. */
  defaultSize: number;
}

interface Run {
  offset: number;
  length: number;
  sizes: number[];
}

/** A top-level box: where it starts in the file and how long it is. */
interface TopBox {
  type: string;
  offset: number;
  header: number;
  size: number;
}

const VIDEO_EXT = /\.(mp4|m4v|mov|qt)$/i;
const READ_BATCH = 8 * 1024 * 1024;
// The moov box is read whole. Hours of video index to a few MB; past this, give up.
const MAX_MOOV = 256 * 1024 * 1024;
const FIRST_BOXES = ["ftyp", "styp", "wide", "free", "skip", "mdat", "moov", "pnot"];

/** Video this can take the audio out of: MP4 or QuickTime. */
export function isExtractableVideo(file: File): boolean {
  return /^video\/(mp4|quicktime|x-m4v)$/.test(file.type) || VIDEO_EXT.test(file.name);
}

export async function extractAudio(file: File): Promise<File | null> {
  const top = await topLevel(file);
  if (!top) return skip("not an MP4/MOV file");
  let track: AudioTrack | null = null;
  // A recorder may leave a fragmented moov at the front and write a complete one at the end
  // when it stops: the complete one indexes everything, so it wins.
  for (const b of top.filter((t) => t.type === "moov")) {
    if (b.size > MAX_MOOV) return skip("moov too large");
    const found = findAudioTrack(await view(file, b.offset + b.header, b.offset + b.size));
    if (typeof found === "string") return skip(found);
    if (found && (!track || (track.fragmented && !found.fragmented))) track = found;
  }
  if (!track) return skip("no audio track");

  const parts: Uint8Array[] = [];
  for await (const run of runsOf(file, top, track)) {
    const buf = new Uint8Array(await file.slice(run.offset, run.offset + run.length).arrayBuffer());
    if (buf.length < run.length) return skip("index points past the end of the file");
    parts.push(frame(track, run, buf));
  }
  if (parts.length === 0) return skip("audio track has no samples");
  const stem = file.name.replace(/\.[^.]+$/, "") || "recording";
  const ext = track.codec === "aac" ? "aac" : "mp3";
  const type = track.codec === "aac" ? "audio/aac" : "audio/mpeg";
  return new File(parts as BlobPart[], `${stem}.${ext}`, { type });
}

function skip(reason: string): null {
  console.warn(`Not extracting audio in the browser: ${reason}. Uploading the file as it is.`);
  return null;
}

/** One run's samples, each behind an ADTS header for AAC. */
function frame(track: AudioTrack, run: Run, buf: Uint8Array): Uint8Array {
  const out = new Uint8Array(run.length + (track.adts ? 7 * run.sizes.length : 0));
  let r = 0;
  let w = 0;
  for (const size of run.sizes) {
    if (track.adts) {
      out.set(adtsHeader(track.adts, size), w);
      w += 7;
    }
    out.set(buf.subarray(r, r + size), w);
    r += size;
    w += size;
  }
  return out;
}

async function view(file: File, start: number, end: number): Promise<DataView> {
  return new DataView(await file.slice(start, end).arrayBuffer());
}

/** The moov's own samples, then (fragmented) each moof's. */
async function* runsOf(file: File, top: TopBox[], t: AudioTrack): AsyncGenerator<Run> {
  yield* tableRuns(t);
  if (t.fragmented) yield* fragmentRuns(file, top, t);
}

/** Byte runs to read from a regular MP4's tables: consecutive chunks merged, each run at most
 * READ_BATCH. */
function* tableRuns(t: AudioTrack): Generator<Run> {
  let sample = 0;
  let cur: Run | null = null;
  for (let chunk = 0; chunk < t.chunkOffsets.length && sample < t.sizes.length; chunk++) {
    const n = samplesInChunk(t.samplesPerChunk, chunk + 1);
    const sizes = t.sizes.slice(sample, sample + n);
    sample += n;
    const length = sizes.reduce((a, b) => a + b, 0);
    const offset = t.chunkOffsets[chunk]!;
    if (cur && cur.offset + cur.length === offset && cur.length + length <= READ_BATCH) {
      cur.length += length;
      cur.sizes.push(...sizes);
    } else {
      if (cur) yield cur;
      cur = { offset, length, sizes };
    }
  }
  if (cur) yield cur;
}

/** Byte runs from a fragmented MP4: each moof's track runs (trun) for this track. Only the
 * moof boxes are read; the video in between is skipped. */
async function* fragmentRuns(file: File, top: TopBox[], t: AudioTrack): AsyncGenerator<Run> {
  for (const b of top.filter((x) => x.type === "moof")) {
    if (b.size > MAX_MOOV) continue;
    const v = await view(file, b.offset, b.offset + b.size);
    const moof: Box = { type: "moof", start: b.header, end: b.size };
    let dataEnd = 0; // where the previous traf's data ended, for the oldest base-offset rule
    for (const traf of children(v, moof.start, moof.end).filter((x) => x.type === "traf")) {
      const tfhd = child(v, traf, "tfhd");
      if (!tfhd) continue;
      const flags = v.getUint32(tfhd.start) & 0xffffff;
      if (v.getUint32(tfhd.start + 4) !== t.id) continue;
      let p = tfhd.start + 8;
      let base = dataEnd || b.offset;
      if (flags & 0x01) {
        base = Number(v.getBigUint64(p));
        p += 8;
      } else if (flags & 0x020000) {
        base = b.offset;
      }
      if (flags & 0x02) p += 4;
      if (flags & 0x08) p += 4;
      const defaultSize = flags & 0x10 ? v.getUint32(p) : t.defaultSize;
      let next = base;
      for (const trun of children(v, traf.start, traf.end).filter((x) => x.type === "trun")) {
        const tf = v.getUint32(trun.start) & 0xffffff;
        const count = v.getUint32(trun.start + 4);
        let q = trun.start + 8;
        if (tf & 0x01) {
          next = base + v.getInt32(q);
          q += 4;
        }
        if (tf & 0x04) q += 4;
        const stride = 4 * [0x100, 0x200, 0x400, 0x800].filter((f) => tf & f).length;
        const sizeAt = tf & 0x100 ? 4 : 0;
        const sizes = new Array<number>(count);
        for (let i = 0; i < count; i++) {
          sizes[i] = tf & 0x200 ? v.getUint32(q + i * stride + sizeAt) : defaultSize;
        }
        const length = sizes.reduce((a, c) => a + c, 0);
        if (count > 0) yield { offset: next, length, sizes };
        next += length;
      }
      dataEnd = next;
    }
  }
}

function samplesInChunk(stsc: [number, number][], chunk: number): number {
  let n = 0;
  for (const [first, count] of stsc) {
    if (first > chunk) break;
    n = count;
  }
  return n;
}

function adtsHeader(a: { profile: number; freq: number; channels: number }, size: number) {
  const len = size + 7;
  if (len > 0x1fff) throw new Error("AAC frame too large for ADTS");
  return Uint8Array.of(
    0xff,
    0xf1,
    (a.profile << 6) | (a.freq << 2) | (a.channels >> 2),
    ((a.channels & 3) << 6) | (len >> 11),
    (len >> 3) & 0xff,
    ((len & 7) << 5) | 0x1f,
    0xfc,
  );
}

// ---- Container -------------------------------------------------------------------------

/** The file's top-level boxes, or null if this is not an ISO-BMFF file. Only box headers are
 * read, so walking past a huge mdat (or thousands of fragments) costs a small read each. */
async function topLevel(file: File): Promise<TopBox[] | null> {
  const out: TopBox[] = [];
  let off = 0;
  while (off + 8 <= file.size) {
    const h = await view(file, off, off + 16);
    let size = h.getUint32(0);
    const type = fourcc(h, 4);
    let header = 8;
    if (size === 1) {
      if (h.byteLength < 16) break;
      size = Number(h.getBigUint64(8));
      header = 16;
    } else if (size === 0) {
      size = file.size - off;
    }
    if (size < header || !/^[\x20-\x7e]{4}$/.test(type)) break; // trailing garbage: stop here
    if (out.length === 0 && !FIRST_BOXES.includes(type)) return null;
    out.push({ type, offset: off, header, size });
    off += size;
  }
  return out.length > 0 ? out : null;
}

function fourcc(v: DataView, at: number): string {
  return String.fromCharCode(v.getUint8(at), v.getUint8(at + 1), v.getUint8(at + 2), v.getUint8(at + 3));
}

function children(v: DataView, start: number, end: number): Box[] {
  const out: Box[] = [];
  let off = start;
  while (off + 8 <= end) {
    let size = v.getUint32(off);
    let header = 8;
    if (size === 1) {
      size = Number(v.getBigUint64(off + 8));
      header = 16;
    } else if (size === 0) {
      size = end - off;
    }
    if (size < header || off + size > end) break;
    out.push({ type: fourcc(v, off + 4), start: off + header, end: off + size });
    off += size;
  }
  return out;
}

function child(v: DataView, box: Box | undefined, ...path: string[]): Box | undefined {
  let cur = box;
  for (const type of path) {
    if (!cur) return undefined;
    cur = children(v, cur.start, cur.end).find((b) => b.type === type);
  }
  return cur;
}

/** The first audio track this can copy; a reason if the audio is in a codec it can't. */
function findAudioTrack(v: DataView): AudioTrack | string | null {
  const trex = new Map<number, number>();
  const mvex = children(v, 0, v.byteLength).find((b) => b.type === "mvex");
  for (const b of mvex ? children(v, mvex.start, mvex.end).filter((x) => x.type === "trex") : []) {
    trex.set(v.getUint32(b.start + 4), v.getUint32(b.start + 16));
  }
  let reason: string | null = null;
  for (const trak of children(v, 0, v.byteLength).filter((b) => b.type === "trak")) {
    const hdlr = child(v, trak, "mdia", "hdlr");
    if (!hdlr || fourcc(v, hdlr.start + 8) !== "soun") continue;
    const tkhd = child(v, trak, "tkhd");
    const stbl = child(v, trak, "mdia", "minf", "stbl");
    if (!tkhd || !stbl) continue;
    const id = v.getUint32(tkhd.start + (v.getUint8(tkhd.start) === 1 ? 20 : 12));
    const track = audioTrack(v, stbl, id, mvex !== undefined, trex.get(id) ?? 0);
    if (typeof track !== "string") return track;
    reason ??= track;
  }
  return reason;
}

function audioTrack(
  v: DataView,
  stbl: Box,
  id: number,
  fragmented: boolean,
  defaultSize: number,
): AudioTrack | string {
  const stsd = child(v, stbl, "stsd");
  // stsd: version/flags, entry count, then sample entries.
  const entry = stsd && children(v, stsd.start + 8, stsd.end)[0];
  if (!entry) return "audio track has no sample description";
  let codec: AudioTrack["codec"];
  let adts: AudioTrack["adts"];
  if (entry.type === ".mp3") {
    codec = "mp3";
  } else if (entry.type === "mp4a") {
    const esds = findEsds(v, entry);
    const config = esds && decoderConfig(v, esds);
    if (!config) return "mp4a track without a readable esds";
    if (config.oti === 0x69 || config.oti === 0x6b) {
      codec = "mp3";
    } else if ([0x40, 0x66, 0x67, 0x68].includes(config.oti) && config.asc) {
      codec = "aac";
      adts = adtsParams(config.asc) ?? undefined;
      if (!adts) return "AAC configuration ADTS can't carry";
    } else {
      return `mp4a object type 0x${config.oti.toString(16)}`;
    }
  } else {
    return `audio codec ${entry.type}`;
  }

  const stsc = child(v, stbl, "stsc");
  const samplesPerChunk: [number, number][] = [];
  const n = stsc ? v.getUint32(stsc.start + 4) : 0;
  for (let i = 0; i < n; i++) {
    const at = stsc!.start + 8 + i * 12;
    samplesPerChunk.push([v.getUint32(at), v.getUint32(at + 4)]);
  }
  return {
    id,
    codec,
    adts,
    sizes: sampleSizes(v, child(v, stbl, "stsz")) ?? [],
    chunkOffsets: offsets(v, child(v, stbl, "stco"), child(v, stbl, "co64")) ?? [],
    samplesPerChunk,
    fragmented,
    defaultSize,
  };
}

/** The esds box in an mp4a sample entry: after the entry's fields, or (QuickTime) in `wave`. */
function findEsds(v: DataView, entry: Box): Box | undefined {
  // Sample entry: 6 reserved + 2 data ref index, then the sound description, whose length
  // depends on its version (QuickTime 1 and 2 add fields after the common 20 bytes).
  const version = v.getUint16(entry.start + 8);
  const fields = 8 + 20 + (version === 1 ? 16 : version === 2 ? 36 : 0);
  for (const b of children(v, entry.start + fields, entry.end)) {
    if (b.type === "esds") return b;
    if (b.type === "wave") {
      const inner = child(v, b, "esds");
      if (inner) return inner;
    }
  }
  return undefined;
}

/** objectTypeIndication and AudioSpecificConfig from an esds box's descriptors. */
function decoderConfig(v: DataView, esds: Box): { oti: number; asc?: Uint8Array } | null {
  let p = esds.start + 4; // version/flags
  const end = esds.end;
  const descriptor = (): { tag: number; len: number } | null => {
    if (p >= end) return null;
    const tag = v.getUint8(p++);
    let len = 0;
    for (let i = 0; i < 4 && p < end; i++) {
      const b = v.getUint8(p++);
      len = (len << 7) | (b & 0x7f);
      if (!(b & 0x80)) break;
    }
    return { tag, len };
  };
  const es = descriptor();
  if (es?.tag !== 0x03) return null;
  p += 2; // ES_ID
  const flags = v.getUint8(p++);
  if (flags & 0x80) p += 2;
  if (flags & 0x40) p += 1 + v.getUint8(p);
  if (flags & 0x20) p += 2;
  const dc = descriptor();
  if (dc?.tag !== 0x04 || p + 13 > end) return null;
  const oti = v.getUint8(p);
  p += 13;
  const dsi = descriptor();
  if (dsi?.tag !== 0x05 || p + dsi.len > end) return { oti };
  return { oti, asc: new Uint8Array(v.buffer, v.byteOffset + p, dsi.len) };
}

/** ADTS fields from an AudioSpecificConfig, or null if ADTS can't carry it. */
export function adtsParams(asc: Uint8Array): { profile: number; freq: number; channels: number } | null {
  if (asc.length < 2) return null;
  const objectType = asc[0]! >> 3;
  const freq = ((asc[0]! & 7) << 1) | (asc[1]! >> 7);
  const channels = (asc[1]! >> 3) & 0xf;
  // HE-AAC (SBR, PS) rides on an AAC-LC core; decoders find the extension implicitly.
  const profile = objectType === 5 || objectType === 29 ? 1 : objectType - 1;
  if (profile < 0 || profile > 3 || freq > 12 || channels < 1 || channels > 7) return null;
  return { profile, freq, channels };
}

function sampleSizes(v: DataView, stsz: Box | undefined): number[] | null {
  if (!stsz) return null;
  const fixed = v.getUint32(stsz.start + 4);
  const count = v.getUint32(stsz.start + 8);
  if (fixed) return new Array<number>(count).fill(fixed);
  if (stsz.start + 12 + count * 4 > stsz.end) return null;
  const out = new Array<number>(count);
  for (let i = 0; i < count; i++) out[i] = v.getUint32(stsz.start + 12 + i * 4);
  return out;
}

function offsets(v: DataView, stco: Box | undefined, co64: Box | undefined): number[] | null {
  const box = stco ?? co64;
  if (!box) return null;
  const count = v.getUint32(box.start + 4);
  const width = stco ? 4 : 8;
  if (box.start + 8 + count * width > box.end) return null;
  const out = new Array<number>(count);
  for (let i = 0; i < count; i++) {
    const at = box.start + 8 + i * width;
    out[i] = stco ? v.getUint32(at) : Number(v.getBigUint64(at));
  }
  return out;
}
