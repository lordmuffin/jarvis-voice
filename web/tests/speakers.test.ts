import { describe, expect, it } from "vitest";
import { initialState, reduce, transcriptLines, type Event, type LiveState } from "../src/reducer";
import type { Segment, SessionDetail, SpeakerInfo } from "../src/types";

const seg = (id: string, start: number, speaker: "me" | "them", over: Partial<Segment> = {}): Segment => ({
  id,
  channel: speaker === "me" ? "mic" : "system",
  speaker,
  start_ms: start,
  end_ms: start + 1000,
  text: id,
  stt_tier: "fake",
  ...over,
});

const voice = (id: string, ordinal: number, over: Partial<SpeakerInfo> = {}): SpeakerInfo => ({
  id,
  label: null,
  enrolled: false,
  is_owner: false,
  ordinal,
  ...over,
});

const state = (segments: Segment[], speakers: SpeakerInfo[], assign: Record<string, string>): LiveState => ({
  ...initialState,
  segments,
  speakers: Object.fromEntries(speakers.map((s) => [s.id, s])),
  assignments: Object.fromEntries(
    Object.entries(assign).map(([seg, sp]) => [seg, { speaker_id: sp, rev: 2, mixed: false }]),
  ),
});

const who = (s: LiveState) => transcriptLines(s).map((l) => [l.who?.text, l.who?.kind]);

describe("who spoke a line", () => {
  it("names a named voice, and calls the owner Me", () => {
    const s = state(
      [seg("a", 0, "me"), seg("b", 2000, "them")],
      [voice("m0", 1, { label: "Andrew", is_owner: true }), voice("s0", 2, { label: "Dana" })],
      { a: "m0", b: "s0" },
    );
    expect(who(s)).toEqual([
      ["Me", "me"],
      ["Dana", "named"],
    ]);
  });

  it("numbers unnamed voices only where a side holds several, with the server's ordinal", () => {
    const s = state(
      [seg("a", 0, "them"), seg("b", 2000, "them"), seg("c", 4000, "me")],
      [voice("s0", 2), voice("s1", 3), voice("m0", 1)],
      { a: "s0", b: "s1", c: "m0" },
    );
    expect(who(s)).toEqual([
      ["Speaker 2", "unknown"],
      ["Speaker 3", "unknown"],
      ["Me", "me"], // one voice on the mic: the side says enough
    ]);
  });

  it("keeps the side for a line nobody has identified", () => {
    const s = state([seg("a", 0, "them")], [], {});
    expect(who(s)).toEqual([["Them", "them"]]);
  });

  it("carries the voice id and whether the line spans a change of speaker", () => {
    const s = state([seg("a", 0, "them")], [voice("s0", 1), voice("s1", 2)], { a: "s0" });
    s.assignments["a"] = { speaker_id: "s0", rev: 2, mixed: true };
    const [line] = transcriptLines(s);
    expect([line!.speaker_id, line!.mixed]).toEqual(["s0", true]);
  });

  it("still lays out a state with no speakers at all", () => {
    const [line] = transcriptLines({ segments: [seg("a", 0, "me")], drafts: [] });
    expect(line!.who).toEqual({ text: "Me", kind: "me" });
  });
});

describe("a snapshot carries the speakers", () => {
  const detail = (over: Partial<SessionDetail>) =>
    ({
      id: "x",
      title: null,
      mode: "meeting",
      status: "done",
      channels: ["mic", "system"],
      started_at: "2026-10-09T14:30:00Z",
      ended_at: null,
      local_only: false,
      streaming: false,
      owner: { id: "u", display_name: "U" },
      segments: [],
      markers: [],
      copilot: { version: 0, notes: [], actions: [], decisions: [], suggestions: [], related: [] },
      final_note: null,
      audio: {},
      publication: null,
      can_publish: false,
      can_regenerate: false,
      ...over,
    }) as SessionDetail;

  it("fills speakers and assignments from the REST detail", () => {
    const s = reduce(
      initialState,
      {
        type: "snapshot",
        detail: detail({
          segments: [seg("a", 0, "them", { speaker_id: "s0", speaker_rev: 2, mixed: true })],
          speakers: [voice("s0", 1, { label: "Dana" })],
        }),
      } as Event,
    );
    expect(s.speakers["s0"]!.label).toBe("Dana");
    expect(s.assignments["a"]).toEqual({ speaker_id: "s0", rev: 2, mixed: true });
  });

  it("does not let an older snapshot undo a newer live assignment", () => {
    const live = reduce(initialState, {
      type: "speaker_update",
      speakers: [],
      assignments: [{ segment_id: "a", speaker_id: "s9", rev: 3, final: true }],
    } as Event);
    const s = reduce(live, {
      type: "snapshot",
      detail: detail({ segments: [seg("a", 0, "them", { speaker_id: "s0", speaker_rev: 2 })] }),
    } as Event);
    expect(s.assignments["a"]!.speaker_id).toBe("s9");
  });

  it("reads a server that sends no speakers as before", () => {
    const s = reduce(initialState, { type: "snapshot", detail: detail({ segments: [seg("a", 0, "me")] }) } as Event);
    expect(s.speakers).toEqual({});
    expect(s.assignments).toEqual({});
  });
});
