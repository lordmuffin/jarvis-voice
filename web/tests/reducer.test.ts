import { readdirSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  initialState,
  reduce,
  transcriptLines,
  type Event,
  type LiveState,
} from "../src/reducer";
import type { Copilot, ServerEvent, SessionDetail } from "../src/types";
import { fixture } from "./fixtures";

const fold = (events: Event[], from: LiveState = initialState) => events.reduce(reduce, from);

const segment = fixture<ServerEvent & { type: "segment" }>("segment");
const draft = fixture<ServerEvent & { type: "draft_segment" }>("draft_segment");
const copilot = fixture<Copilot & { type: "copilot" }>("copilot");
const emptyCopilot = fixture<Copilot & { type: "copilot" }>("copilot__empty");

const detail = (over: Partial<SessionDetail> = {}): SessionDetail => ({
  id: "3f2b8c1e-5d4a-4b7e-9a10-2c6d8e9f0a1b",
  title: "Weekly sync",
  mode: "meeting",
  status: "live",
  channels: ["mic", "system"],
  started_at: "2026-10-09T14:30:00Z",
  ended_at: null,
  local_only: false,
  streaming: true,
  segments: [],
  copilot: { version: 0, notes: [], actions: [], decisions: [], suggestions: [], related: [] },
  markers: [],
  final_note: null,
  ...over,
});

describe("every server event type", () => {
  it("is handled without throwing and keeps unrelated state", () => {
    for (const name of [
      "hello_ack",
      "ack",
      "segment",
      "copilot",
      "status",
      "final_note",
      "error",
      "draft_segment",
      "marker",
    ]) {
      const ev = fixture<ServerEvent>(name);
      const next = reduce(initialState, ev);
      expect(next, name).not.toBe(initialState);
    }
  });

  it("covers every server->client fixture in protocol/v1/fixtures/valid", () => {
    // If a new server message is added to the protocol its fixture must be handled above.
    const dir = new URL("../../protocol/v1/fixtures/valid/", import.meta.url);
    const types = readdirSync(dir)
      .filter((f) => f.endsWith(".json"))
      .map((f) => fixture<{ type?: string }>(f.replace(/\.json$/, "")).type)
      .filter((t): t is string => !!t);
    const clientOnly = new Set(["hello", "end"]);
    for (const t of types.filter((t) => !clientOnly.has(t))) {
      const ev = fixture<ServerEvent>(t);
      expect(reduce(initialState, ev), t).not.toBe(initialState);
    }
  });

  it("ignores unknown events", () => {
    const weird = { type: "future_thing" } as unknown as Event;
    expect(reduce(initialState, weird)).toBe(initialState);
  });
});

describe("hello_ack / ack / status / error / final_note / marker", () => {
  it("tracks acked seqs, only moving forward", () => {
    let s = reduce(initialState, fixture<ServerEvent>("hello_ack"));
    expect(s.acked).toEqual({ mic: 41, system: null });
    s = reduce(s, fixture<ServerEvent>("ack")); // mic seq 42
    expect(s.acked.mic).toBe(42);
    const same = reduce(s, { type: "ack", channel: "mic", seq: 10 });
    expect(same).toBe(s);
  });

  it("keeps the latest status, final note and error", () => {
    const s = fold([
      fixture<ServerEvent>("status__no_tier"),
      fixture<ServerEvent>("status"),
      fixture<ServerEvent>("final_note"),
      fixture<ServerEvent>("error"),
    ]);
    expect(s.status).toEqual({ stt_tier: "local", llm_ok: true, lag_ms: 180 });
    expect(s.finalNote).toEqual({ path: "Meetings/2026-10-09 Weekly sync.md", title: "Weekly sync" });
    expect(s.error).toEqual({ code: "bad_frame", message: "frame too short" });
  });

  it("collects markers in time order without duplicates", () => {
    const m = fixture<ServerEvent>("marker");
    const s = fold([
      { type: "marker", t_ms: 9000, label: "later" },
      m,
      m,
    ]);
    expect(s.markers).toEqual([
      { t_ms: 5000, label: "decision" },
      { t_ms: 9000, label: "later" },
    ]);
  });
});

describe("segments and drafts", () => {
  it("shows a draft until a final segment overlaps it, then replaces it", () => {
    // draft_segment fixture: mic 1000-2400 "let's get started"; segment fixture is on `system`.
    const micSegment: ServerEvent = {
      ...(segment as object),
      type: "segment",
      id: "seg_mic",
      channel: "mic",
      speaker: "me",
      start_ms: 900,
      end_ms: 2500,
      text: "Let's get started.",
    } as ServerEvent;

    let s = fold([draft]);
    expect(s.drafts).toHaveLength(1);
    expect(transcriptLines(s).map((l) => [l.kind, l.text])).toEqual([["draft", "let's get started"]]);

    // A final segment on another channel does not replace it.
    s = reduce(s, segment);
    expect(s.drafts).toHaveLength(1);
    expect(s.segments).toHaveLength(1);

    s = reduce(s, micSegment);
    expect(s.drafts).toHaveLength(0);
    expect(transcriptLines(s).map((l) => [l.kind, l.id])).toEqual([
      ["segment", "seg_mic"],
      ["segment", "seg_1"],
    ]);
  });

  it("lets a growing draft supersede the earlier one on the same channel", () => {
    const d1 = { ...draft, end_ms: 1800, text: "let's" };
    const d2 = { ...draft, end_ms: 2400, text: "let's get started" };
    const s = fold([d1, d2]);
    expect(s.drafts.map((d) => d.text)).toEqual(["let's get started"]);
  });

  it("is idempotent for repeated segments and orders by start_ms", () => {
    const late = { ...segment, id: "seg_2", start_ms: 5000, end_ms: 6000, text: "second" };
    const s = fold([late, segment, segment]);
    expect(s.segments.map((x) => x.id)).toEqual(["seg_1", "seg_2"]);
    expect(reduce(s, segment)).toBe(s);
  });

  it("replaces a segment re-sent with the same id (corrected text)", () => {
    const fixed = { ...segment, text: "Hello, everyone" };
    const s = fold([segment, fixed]);
    expect(s.segments).toHaveLength(1);
    expect(s.segments[0]!.text).toBe("Hello, everyone");
  });

  it("advances the stream clock", () => {
    expect(fold([segment]).streamMs).toBe(2400);
    expect(fold([draft, { ...draft, start_ms: 5000, end_ms: 7000 }]).streamMs).toBe(7000);
  });
});

describe("copilot snapshots", () => {
  it("replaces state with a snapshot of a higher version", () => {
    const s = fold([emptyCopilot, copilot]);
    expect(s.copilot.version).toBe(3);
    expect(s.copilot.notes).toEqual([{ id: "n1", text: "Kickoff" }]);
    expect(s.copilot.actions[0]).toMatchObject({ id: "a1", owner: "sam", due: "2026-10-16" });
  });

  it("ignores equal and lower versions (out-of-order delivery)", () => {
    const v3 = reduce(initialState, copilot);
    const stale = { ...copilot, version: 2, notes: [{ id: "n9", text: "stale" }] };
    const dup = { ...copilot, notes: [{ id: "n9", text: "dup" }] };
    expect(reduce(v3, stale)).toBe(v3);
    expect(reduce(v3, dup)).toBe(v3);
  });

  it("accepts the first snapshot even when it is version 0", () => {
    const s = reduce(initialState, emptyCopilot);
    expect(s.copilot.version).toBe(0);
  });

  it("replaces, not merges: removed items disappear", () => {
    const smaller = { ...copilot, version: 4, notes: [], actions: [] };
    const s = fold([copilot, smaller]);
    expect(s.copilot.notes).toEqual([]);
    expect(s.copilot.actions).toEqual([]);
    expect(s.copilot.decisions).toHaveLength(1);
  });
});

describe("snapshot bootstrap (REST) merged with the live stream", () => {
  it("does not regress copilot or duplicate segments when the stream was ahead", () => {
    const ahead = fold([copilot, segment]);
    const s = reduce(
      ahead,
      {
        type: "snapshot",
        detail: detail({
          segments: [{ ...segment, id: "seg_1", stt_tier: "local" }],
          copilot: { ...copilot, version: 1, notes: [] },
        }),
      },
    );
    expect(s.copilot.version).toBe(3);
    expect(s.segments).toHaveLength(1);
  });

  it("brings in segments, markers and the final note from a finished session", () => {
    const s = reduce(initialState, {
      type: "snapshot",
      detail: detail({
        status: "done",
        segments: [{ ...segment, id: "a" }],
        markers: [{ t_ms: 1, label: "x" }],
        copilot,
        final_note: { path: "n.md", title: "T", markdown: "# T" },
      }),
    });
    expect(s.segments).toHaveLength(1);
    expect(s.markers).toEqual([{ t_ms: 1, label: "x" }]);
    expect(s.copilot.version).toBe(3);
    expect(s.finalNote).toEqual({ path: "n.md", title: "T" });
  });

  it("tracks the session row: title, status and streaming follow each poll", () => {
    let s = reduce(initialState, { type: "snapshot", detail: detail({ title: null }) });
    expect(s.session).toMatchObject({ title: null, status: "live", streaming: true });
    expect(s.session).not.toHaveProperty("segments");
    s = reduce(s, {
      type: "snapshot",
      detail: detail({ title: "Budget review", streaming: false }),
    });
    expect(s.session).toMatchObject({ title: "Budget review", status: "live", streaming: false });
    s = reduce(s, { type: "snapshot", detail: detail({ status: "finalizing", streaming: false }) });
    expect(s.session?.status).toBe("finalizing");
  });

  it("applies a rename immediately", () => {
    const s0 = reduce(initialState, { type: "snapshot", detail: detail() });
    const { segments: _s, copilot: _c, markers: _m, final_note: _f, ...row } = detail();
    const s = reduce(s0, { type: "session", session: { ...row, title: "Renamed" } });
    expect(s.session?.title).toBe("Renamed");
    expect(s.segments).toBe(s0.segments);
  });

  it("does not mutate the previous state", () => {
    const before = JSON.stringify(initialState);
    fold([segment, draft, copilot, fixture<ServerEvent>("marker")]);
    expect(JSON.stringify(initialState)).toBe(before);
  });
});

describe("connection", () => {
  it("records connection lifecycle", () => {
    const s = fold([
      { type: "connection", state: "connecting" },
      { type: "connection", state: "open" },
    ]);
    expect(s.connection).toBe("open");
  });
});
