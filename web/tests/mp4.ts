/** A minimal MP4 writer, enough to lay out tracks the way recorders do. */

export const enc = new TextEncoder();
const u32 = (n: number) => Uint8Array.of(n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255);
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

export function trak(handler: string, entry: Uint8Array, sizes: number[], stsc: [number, number][], chunks: number[]) {
  const stbl = box(
    "stbl",
    full("stsd", u32(1), entry),
    full("stsz", u32(0), u32(sizes.length), ...sizes.map(u32)),
    full("stsc", u32(stsc.length), ...stsc.map(([f, n]) => cat(u32(f), u32(n), u32(1)))),
    full("stco", u32(chunks.length), ...chunks.map(u32)),
  );
  return box(
    "trak",
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
