import { describe, expect, it } from "vitest";
import { adtsParams, extractAudio, isExtractableVideo } from "../src/extractAudio";
import { ASC_LC_48K_STEREO, box, cat, enc, esds, fragmentedMp4, mp4, soundEntry, trak } from "./mp4";

const frames = [3, 5, 2, 4].map((n, i) => new Uint8Array(n).fill(i + 1));
const file = (bytes: Uint8Array, name = "clip.mp4", type = "video/mp4") =>
  new File([bytes as BlobPart], name, { type });
const bytesOf = async (f: File) => new Uint8Array(await f.arrayBuffer());

/** Split an ADTS stream into [header, payload] pairs. */
function adtsFrames(b: Uint8Array) {
  const out: { header: Uint8Array; payload: Uint8Array }[] = [];
  for (let o = 0; o < b.length; ) {
    const len = ((b[o + 3]! & 3) << 11) | (b[o + 4]! << 3) | (b[o + 5]! >> 5);
    out.push({ header: b.subarray(o, o + 7), payload: b.subarray(o + 7, o + len) });
    o += len;
  }
  return out;
}

describe("extractAudio", () => {
  it("copies an AAC track out of a video as ADTS, skipping the video's bytes", async () => {
    const out = (await extractAudio(file(mp4({ audio: frames }), "Board meeting.mp4")))!;
    expect(out.name).toBe("Board meeting.aac");
    expect(out.type).toBe("audio/aac");
    const got = adtsFrames(await bytesOf(out));
    expect(got.map((f) => [...f.payload])).toEqual(frames.map((f) => [...f]));
    // syncword, MPEG-4, no CRC; LC (profile 1), 48 kHz (3), 2 channels
    const h = got[0]!.header;
    expect([h[0], h[1]]).toEqual([0xff, 0xf1]);
    expect(h[2]! >> 6).toBe(1);
    expect((h[2]! >> 2) & 0xf).toBe(3);
    expect(((h[2]! & 1) << 2) | (h[3]! >> 6)).toBe(2);
  });

  it("finds the index wherever it is in the file", async () => {
    const out = (await extractAudio(file(mp4({ audio: frames, moovFirst: true }))))!;
    expect(adtsFrames(await bytesOf(out)).map((f) => f.payload.length)).toEqual([3, 5, 2, 4]);
  });

  it("finds a QuickTime esds inside the wave box", async () => {
    const entry = soundEntry("mp4a", box("wave", box("frma", enc.encode("mp4a")), esds(0x40, ASC_LC_48K_STEREO)));
    const out = (await extractAudio(file(mp4({ audio: frames, audioEntry: entry }), "a.mov", "video/quicktime")))!;
    expect(adtsFrames(await bytesOf(out))).toHaveLength(4);
  });

  it("concatenates MP3 frames as they are", async () => {
    const entry = soundEntry("mp4a", esds(0x6b));
    const out = (await extractAudio(file(mp4({ audio: frames, audioEntry: entry }))))!;
    expect(out.name).toBe("clip.mp3");
    expect([...(await bytesOf(out))]).toEqual([...frames.flatMap((f) => [...f])]);
  });

  it("gives up on what it can't copy, so the caller sends the file as it is", async () => {
    expect(await extractAudio(file(enc.encode("\x1aE\xdf\xa3 matroska, not mp4"), "a.mkv"))).toBeNull();
    const opus = soundEntry("Opus", box("dOps", new Uint8Array(11)));
    expect(await extractAudio(file(mp4({ audio: frames, audioEntry: opus })))).toBeNull();
    // An audio track with no samples anywhere.
    const empty = box("moov", trak("soun", soundEntry("mp4a", esds(0x40, ASC_LC_48K_STEREO)), [], [], []));
    expect(await extractAudio(file(cat(box("ftyp", enc.encode("iso5")), empty)))).toBeNull();
  });

  describe("fragmented MP4 (OBS, screen recorders)", () => {
    const frags = [frames.slice(0, 2), frames.slice(2)];
    const payloads = async (f: File | null) => adtsFrames(await bytesOf(f!)).map((x) => [...x.payload]);

    it.each([
      ["sizes per sample, offsets from the moof", { base: "moof", sizes: "trun" }],
      ["an explicit base offset", { base: "explicit", sizes: "trun" }],
    ] as const)("reads each moof's audio run, skipping the video: %s", async (_, opts) => {
      expect(await payloads(await extractAudio(file(fragmentedMp4(frags, opts))))).toEqual(frames.map((f) => [...f]));
    });

    it.each([["tfhd"], ["trex"]] as const)("takes a fixed sample size from %s", async (sizes) => {
      const same = [0, 1, 2, 3].map((i) => new Uint8Array(4).fill(i + 1));
      const got = await payloads(await extractAudio(file(fragmentedMp4([same.slice(0, 2), same.slice(2)], { sizes }))));
      expect(got).toEqual(same.map((f) => [...f]));
    });

    it("reads the moov's own samples first, then the fragments", async () => {
      const head = [Uint8Array.of(9, 9), Uint8Array.of(8)];
      const got = await payloads(await extractAudio(file(fragmentedMp4(frags, { moovSamples: head }))));
      expect(got).toEqual([...head, ...frames].map((f) => [...f]));
    });

    it("reads a file with both a fragmented and a complete moov without doubling the audio", async () => {
      // As a recorder leaves it when it stops cleanly: fragments, then one full index.
      const fragmented = fragmentedMp4(frags);
      const sizes = frames.map((f) => f.length);
      const offs: number[] = [];
      let o = 0;
      for (let i = 0; i < fragmented.length; ) {
        const v = new DataView(fragmented.buffer, i);
        const size = v.getUint32(0);
        const type = String.fromCharCode(...fragmented.subarray(i + 4, i + 8));
        if (type === "mdat") {
          let p = i + 8 + 100; // past the video
          for (let k = 0; k < 2; k++) {
            offs.push(p);
            p += sizes[o++]!;
          }
        }
        i += size;
      }
      const complete = box(
        "moov",
        trak("soun", soundEntry("mp4a", esds(0x40, ASC_LC_48K_STEREO)), sizes, [[1, 1]], offs),
      );
      const got = await payloads(await extractAudio(file(cat(fragmented, complete))));
      expect(got).toEqual(frames.map((f) => [...f]));
    });
  });
});

describe("adtsParams", () => {
  it("carries HE-AAC on its LC core and refuses what ADTS can't express", () => {
    expect(adtsParams(ASC_LC_48K_STEREO)).toEqual({ profile: 1, freq: 3, channels: 2 });
    // HE-AAC v1: object type 5, 24 kHz core (6), stereo
    expect(adtsParams(Uint8Array.of(0x2b, 0x10, 0x88, 0x00))).toEqual({ profile: 1, freq: 6, channels: 2 });
    // channel config 0 (layout in a PCE) and escape object types aren't representable
    expect(adtsParams(Uint8Array.of(0x11, 0x80))).toBeNull();
    expect(adtsParams(Uint8Array.of(0xf8, 0x00))).toBeNull();
  });
});

describe("isExtractableVideo", () => {
  it("takes MP4 and QuickTime by type or name", () => {
    expect(isExtractableVideo(file(new Uint8Array(), "a.bin", "video/mp4"))).toBe(true);
    expect(isExtractableVideo(file(new Uint8Array(), "IMG_0042.MOV", ""))).toBe(true);
    expect(isExtractableVideo(file(new Uint8Array(), "memo.m4a", "audio/mp4"))).toBe(false);
    expect(isExtractableVideo(file(new Uint8Array(), "talk.webm", "video/webm"))).toBe(false);
  });
});
