/**
 * Pull the audio track out of an MP4/MOV video in the browser, so a multi-gigabyte video
 * uploads as tens of megabytes. The server keeps only the audio anyway.
 *
 * No decoding: the track's compressed frames are copied out as they are. AAC frames get ADTS
 * headers (a `.aac` stream); MP3 frames are self-framing and are simply concatenated. Only the
 * container's index and the audio bytes are read (`File.slice`), never the whole file.
 *
 * Returns null when the file is not something this can copy (another container, a fragmented
 * MP4, an audio codec other than AAC/MP3); the caller then uploads the file as it is.
 */

interface Box {
  type: string;
  /** Offset of the box's payload, after its header. */
  start: number;
  end: number;
}

interface AudioTrack {
  codec: "aac" | "mp3";
  /** AAC only: the 7-byte ADTS header's fixed part (profile, rate, channels). */
  adts?: { profile: number; freq: number; channels: number };
  sizes: number[];
  chunkOffsets: number[];
  /** stsc: [first chunk (1-based), samples per chunk]. */
  samplesPerChunk: [number, number][];
}

const VIDEO_EXT = /\.(mp4|m4v|mov|qt)$/i;
const READ_BATCH = 8 * 1024 * 1024;
// The moov box is read whole. Hours of video index to a few MB; past this, give up.
const MAX_MOOV = 256 * 1024 * 1024;

/** Video this can take the audio out of: MP4 or QuickTime. */
export function isExtractableVideo(file: File): boolean {
  return /^video\/(mp4|quicktime|x-m4v)$/.test(file.type) || VIDEO_EXT.test(file.name);
}

export async function extractAudio(
  file: File,
  onProgress?: (done: number, total: number) => void,
): Promise<File | null> {
  const moov = await readMoov(file);
  if (!moov) return null;
  const track = findAudioTrack(moov);
  if (!track || track.sizes.length === 0) return null;

  const parts: Uint8Array[] = [];
  const total = track.sizes.reduce((a, b) => a + b, 0);
  let done = 0;
  for (const run of runs(track)) {
    const buf = new Uint8Array(await file.slice(run.offset, run.offset + run.length).arrayBuffer());
    if (buf.length < run.length) return null; // index points past the end: truncated file
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
    parts.push(out);
    done += run.length;
    onProgress?.(done, total);
  }
  const stem = file.name.replace(/\.[^.]+$/, "") || "recording";
  const ext = track.codec === "aac" ? "aac" : "mp3";
  const type = track.codec === "aac" ? "audio/aac" : "audio/mpeg";
  return new File(parts as BlobPart[], `${stem}.${ext}`, { type });
}

/** Byte runs to read: consecutive chunks merged, each run at most READ_BATCH. */
function* runs(t: AudioTrack): Generator<{ offset: number; length: number; sizes: number[] }> {
  let sample = 0;
  let cur: { offset: number; length: number; sizes: number[] } | null = null;
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

/** The moov box's bytes, or null if this is not an ISO-BMFF file. Only box headers are read
 * on the way, so a moov after a huge mdat costs a few small reads. */
async function readMoov(file: File): Promise<DataView | null> {
  let off = 0;
  let first = true;
  while (off + 8 <= file.size) {
    const h = new DataView(await file.slice(off, off + 16).arrayBuffer());
    let size = h.getUint32(0);
    const type = fourcc(h, 4);
    let header = 8;
    if (size === 1) {
      if (h.byteLength < 16) return null;
      size = Number(h.getBigUint64(8));
      header = 16;
    } else if (size === 0) {
      size = file.size - off;
    }
    if (size < header || !/^[\x20-\x7e]{4}$/.test(type)) return null;
    if (first && !["ftyp", "wide", "free", "skip", "mdat", "moov", "pnot"].includes(type)) return null;
    first = false;
    if (type === "moov") {
      if (size > MAX_MOOV) return null;
      return new DataView(await file.slice(off + header, off + size).arrayBuffer());
    }
    off += size;
  }
  return null;
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

function findAudioTrack(v: DataView): AudioTrack | null {
  for (const trak of children(v, 0, v.byteLength).filter((b) => b.type === "trak")) {
    const hdlr = child(v, trak, "mdia", "hdlr");
    if (!hdlr || fourcc(v, hdlr.start + 8) !== "soun") continue;
    const stbl = child(v, trak, "mdia", "minf", "stbl");
    const track = stbl && audioTrack(v, stbl);
    if (track) return track;
  }
  return null;
}

function audioTrack(v: DataView, stbl: Box): AudioTrack | null {
  const stsd = child(v, stbl, "stsd");
  if (!stsd) return null;
  // stsd: version/flags, entry count, then sample entries.
  const entry = children(v, stsd.start + 8, stsd.end)[0];
  if (!entry) return null;
  let codec: AudioTrack["codec"];
  let adts: AudioTrack["adts"];
  if (entry.type === ".mp3") {
    codec = "mp3";
  } else if (entry.type === "mp4a") {
    const esds = findEsds(v, entry);
    const config = esds && decoderConfig(v, esds);
    if (!config) return null;
    if (config.oti === 0x69 || config.oti === 0x6b) {
      codec = "mp3";
    } else if ([0x40, 0x66, 0x67, 0x68].includes(config.oti) && config.asc) {
      codec = "aac";
      adts = adtsParams(config.asc) ?? undefined;
      if (!adts) return null;
    } else {
      return null;
    }
  } else {
    return null;
  }

  const sizes = sampleSizes(v, child(v, stbl, "stsz"));
  const chunkOffsets = offsets(v, child(v, stbl, "stco"), child(v, stbl, "co64"));
  const stsc = child(v, stbl, "stsc");
  if (!sizes || !chunkOffsets || !stsc) return null;
  const samplesPerChunk: [number, number][] = [];
  const n = v.getUint32(stsc.start + 4);
  for (let i = 0; i < n; i++) {
    const at = stsc.start + 8 + i * 12;
    samplesPerChunk.push([v.getUint32(at), v.getUint32(at + 4)]);
  }
  return { codec, adts, sizes, chunkOffsets, samplesPerChunk };
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
