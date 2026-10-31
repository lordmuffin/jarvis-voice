/** A minimal MP4 writer, enough to lay out tracks the way recorders do. */

export const enc = new TextEncoder();
export const u32 = (n: number) => Uint8Array.of(n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255);
const u16 = (n: number) => Uint8Array.of(n >>> 8, n & 255);
export const cat = (...parts: Uint8Array[]) => {
  const out = new Uint8Array(parts.reduce((a, p) => a + p.length, 0));
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
};
export const box = (type: string, ...body: Uint8Array[]) => {
  const payload = cat(...body);
  return cat(u32(payload.length + 8), enc.encode(type), payload);
};
export const full = (type: string, ...body: Uint8Array[]) => box(type, u32(0), ...body);

// AAC LC, 48 kHz (index 3), stereo: 00010 0011 0010 000
export const ASC_LC_48K_STEREO = Uint8Array.of(0x11, 0x90);

export function esds(oti: number, asc?: Uint8Array) {
  const dsi = asc ? cat(Uint8Array.of(0x05, asc.length), asc) : new Uint8Array();
  const dc = cat(Uint8Array.of(0x04, 13 + dsi.length, oti, 0x15), new Uint8Array(11), dsi);
  return full("esds", cat(Uint8Array.of(0x03, 3 + dc.length), u16(1), Uint8Array.of(0), dc));
}

export function soundEntry(type: string, ...extra: Uint8Array[]) {
  // reserved(6) + data ref(2) + version 0 sound description (20)
  return box(type, new Uint8Array(6), u16(1), new Uint8Array(20), ...extra);
}

export function trak(
  handler: string,
  entry: Uint8Array,
  sizes: number[],
  stsc: [number, number][],
  chunks: number[],
  id = handler === "soun" ? 2 : 1,
) {
  const stbl = box(
    "stbl",
    full("stsd", u32(1), entry),
    full("stsz", u32(0), u32(sizes.length), ...sizes.map(u32)),
    full("stsc", u32(stsc.length), ...stsc.map(([f, n]) => cat(u32(f), u32(n), u32(1)))),
    full("stco", u32(chunks.length), ...chunks.map(u32)),
  );
  // tkhd v0: version/flags, created, modified, track id, ...
  const tkhd = full("tkhd", u32(0), u32(0), u32(id), new Uint8Array(68));
  return box(
    "trak",
    tkhd,
    box("mdia", full("hdlr", u32(0), enc.encode(handler), new Uint8Array(12)), box("minf", stbl)),
  );
}

/** ftyp + mdat (+ moov, before or after), with samples at the offsets the tables say. */
export function mp4(opts: {
  audio: Uint8Array[];
  audioEntry?: Uint8Array;
  moovFirst?: boolean;
}) {
  const ftyp = box("ftyp", enc.encode("isom"), u32(0), enc.encode("isom"));
  const video = new Uint8Array(100).fill(0xee);
  // Interleave: audio chunk 1 (two frames), video, audio chunk 2 (the rest).
  const [a, b, ...rest] = opts.audio;
  const chunk1 = cat(a!, b!);
  const chunk2 = cat(...rest);
  const mdatBody = cat(chunk1, video, chunk2);
  const build = (mdatAt: number) => {
    const base = mdatAt + 8;
    return box(
      "moov",
      trak("vide", box("avc1", new Uint8Array(70)), [video.length], [[1, 1]], [base + chunk1.length]),
      trak(
        "soun",
        opts.audioEntry ?? soundEntry("mp4a", esds(0x40, ASC_LC_48K_STEREO)),
        opts.audio.map((s) => s.length),
        [
          [1, 2],
          [2, rest.length],
        ],
        [base, base + chunk1.length + video.length],
      ),
    );
  };
  const mdat = box("mdat", mdatBody);
  if (!opts.moovFirst) return cat(ftyp, mdat, build(ftyp.length));
  const moovLen = build(0).length;
  return cat(ftyp, build(ftyp.length + moovLen), mdat);
}

const AAC_ENTRY = () => soundEntry("mp4a", esds(0x40, ASC_LC_48K_STEREO));
const trex = (id: number, size: number) => full("trex", u32(id), u32(1), u32(1024), u32(size), u32(0));

/**
 * A fragmented MP4 the way recorders stream it: a moov with mvex and empty tables, then a
 * moof + mdat pair per fragment, each holding video and audio. Fragment `i` holds
 * `fragments[i]` audio frames after 100 bytes of video. Variants cover the ways a traf
 * says where its data is and how big its samples are.
 */
export function fragmentedMp4(
  fragments: Uint8Array[][],
  opts: { base?: "moof" | "explicit"; sizes?: "trun" | "tfhd" | "trex"; moovSamples?: Uint8Array[] } = {},
) {
  const base = opts.base ?? "moof";
  const sizesIn = opts.sizes ?? "trun";
  const ftyp = box("ftyp", enc.encode("iso5"), u32(0), enc.encode("iso5"));
  const fixed = fragments[0]?.[0]?.length ?? 0;
  // Samples indexed by the moov itself (frag_keyframe without empty_moov) sit in an mdat
  // right after it.
  const head = opts.moovSamples ?? [];
  const moovFor = (mdatAt: number) =>
    box(
      "moov",
      trak("vide", box("avc1", new Uint8Array(70)), [], [], []),
      trak("soun", AAC_ENTRY(), head.map((h) => h.length), head.length ? [[1, head.length]] : [], head.length ? [mdatAt + 8] : []),
      box("mvex", trex(1, 0), trex(2, sizesIn === "trex" ? fixed : 0)),
    );
  const moovLen = moovFor(0).length;
  const out: Uint8Array[] = [ftyp, moovFor(ftyp.length + moovLen)];
  if (head.length) out.push(box("mdat", ...head));
  let at = out.reduce((a, b) => a + b.length, 0);
  const video = new Uint8Array(100).fill(0xee);
  for (const frames of fragments) {
    const build = (dataOffsetVideo: number, dataOffsetAudio: number) => {
      const traf = (id: number, sizes: number[], dataOffset: number) => {
        const flags = (base === "moof" ? 0x020000 : 0x01) | (sizesIn === "tfhd" && id === 2 ? 0x10 : 0);
        const tfhd = box(
          "tfhd",
          u32(flags),
          u32(id),
          ...(base === "explicit" ? [u32(0), u32(at)] : []),
          ...(flags & 0x10 ? [u32(fixed)] : []),
        );
        const perSample = sizesIn === "trun" || id === 1;
        const trun = box(
          "trun",
          u32(0x01 | (perSample ? 0x200 : 0)),
          u32(sizes.length),
          u32(dataOffset),
          ...(perSample ? sizes.map(u32) : []),
        );
        return box("traf", tfhd, trun);
      };
      return box(
        "moof",
        full("mfhd", u32(1)),
        traf(1, [video.length], dataOffsetVideo),
        traf(2, frames.map((f) => f.length), dataOffsetAudio),
      );
    };
    const moofLen = build(0, 0).length;
    // Offsets are from the moof's start either way (explicit base = the moof's offset).
    const moof = build(moofLen + 8, moofLen + 8 + video.length);
    const mdat = box("mdat", video, ...frames);
    out.push(moof, mdat);
    at += moof.length + mdat.length;
  }
  return cat(...out);
}
