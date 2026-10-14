import { describe, expect, it } from "vitest";
import { dockView, type DockInput } from "../src/jarvis/dock";
import type { Suggestion } from "../src/types";

const s = (id: string, expires: number, kind: Suggestion["kind"] = "question"): Suggestion => ({
  id,
  kind,
  text: `text ${id}`,
  expires_at_ms: expires,
});

const base = (over: Partial<DockInput> = {}): DockInput => ({
  suggestions: [],
  dismissed: new Set(),
  holds: {},
  version: 3,
  streamMs: 100_000,
  arrivals: {},
  lookedAt: 0,
  ...over,
});

describe("dockView", () => {
  it("is empty with nothing waiting", () => {
    expect(dockView(base())).toEqual({ lead: null, more: [] });
  });

  it("leads with the newest suggestion and ages the older ones onto the lines below", () => {
    const v = dockView(
      base({
        suggestions: [s("old", 400_000), s("new", 400_000)],
        arrivals: { "suggestion:old": { at: 50_000, initial: false, wall: 50_000 }, "suggestion:new": { at: 90_000, initial: false, wall: 90_000 } },
        lookedAt: 95_000,
      }),
    );
    expect(v.lead).toMatchObject({ id: "new", state: "ageing", ageMs: 10_000 });
    expect(v.more.map((m) => [m.id, m.state])).toEqual([["old", "ageing"]]);
  });

  it("calls a suggestion new when it arrived after you looked and was not there at load", () => {
    const v = dockView(
      base({
        suggestions: [s("a", 400_000), s("b", 400_000)],
        arrivals: { "suggestion:a": { at: 10_000, initial: true, wall: 10_000 }, "suggestion:b": { at: 99_000, initial: false, wall: 99_000 } },
        lookedAt: 50_000,
      }),
    );
    expect(v.lead).toMatchObject({ id: "b", state: "new" });
    expect(v.more[0]).toMatchObject({ id: "a", state: "ageing" });
  });

  it("shows an expired suggestion as Expired until the next copilot update, then drops it", () => {
    const sug = [s("x", 90_000)];
    const held = { x: { kind: "expired" as const, version: 3 } };
    expect(dockView(base({ suggestions: sug, holds: held })).lead).toMatchObject({ id: "x", state: "expired" });
    expect(dockView(base({ suggestions: sug, holds: held, version: 4 })).lead).toBeNull();
  });

  it("keeps a just-dismissed suggestion in its place, quiet, until the next update", () => {
    const sug = [s("q", 400_000)];
    const dismissed = new Set(["q"]);
    const held = { q: { kind: "dismissed" as const, version: 3 } };
    expect(dockView(base({ suggestions: sug, dismissed, holds: held })).lead).toMatchObject({ id: "q", state: "dismissed" });
    expect(dockView(base({ suggestions: sug, dismissed, holds: held, version: 4 })).lead).toBeNull();
  });

  it("keeps a dismissal from an earlier visit hidden", () => {
    expect(dockView(base({ suggestions: [s("q", 400_000)], dismissed: new Set(["q"]) })).lead).toBeNull();
  });

  it("promotes the next suggestion while a dismissed one waits for the next update", () => {
    const v = dockView(
      base({
        suggestions: [s("q", 400_000), s("r", 400_000)],
        dismissed: new Set(["q"]),
        holds: { q: { kind: "dismissed", version: 3 } },
        arrivals: { "suggestion:q": { at: 90_000, initial: false, wall: 90_000 }, "suggestion:r": { at: 80_000, initial: false, wall: 80_000 } },
      }),
    );
    expect(v.lead).toMatchObject({ id: "r" });
    expect(v.more.map((m) => m.state)).toEqual(["dismissed"]);
  });

  it("breaks a tie in stream time by which arrived later", () => {
    const v = dockView(
      base({
        suggestions: [s("first", 4e5), s("second", 4e5)],
        arrivals: {
          "suggestion:first": { at: 5000, initial: true, wall: 1000 },
          "suggestion:second": { at: 5000, initial: false, wall: 2000 },
        },
      }),
    );
    expect(v.lead!.id).toBe("second");
  });

  it("shows at most one older line under the lead", () => {
    const sug = [s("a", 4e5), s("b", 4e5), s("c", 4e5)];
    const arrivals = Object.fromEntries(sug.map((x, i) => [`suggestion:${x.id}`, { at: i * 1000, initial: false, wall: i * 1000 }]));
    const v = dockView(base({ suggestions: sug, arrivals }));
    expect(v.lead!.id).toBe("c");
    expect(v.more.map((m) => m.id)).toEqual(["b"]);
  });
});
