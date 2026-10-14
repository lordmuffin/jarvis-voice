import { describe, expect, it } from "vitest";
import {
  buildFeed,
  copilotInsights,
  filterCounts,
  groupInsights,
  markNew,
  trackArrivals,
  workInsights,
  type Insight,
} from "../src/feed";
import type { Line } from "../src/reducer";
import type { Copilot, Marker } from "../src/types";

const copilot = (over: Partial<Copilot> = {}): Copilot => ({
  version: 1,
  notes: [],
  actions: [],
  decisions: [],
  suggestions: [],
  related: [],
  ...over,
});

const line = (key: string, sortMs: number, text = key): Line => ({
  kind: "segment",
  key,
  id: key,
  sortMs,
  start_ms: sortMs,
  end_ms: sortMs + 500,
  speaker: "me",
  text,
});

describe("trackArrivals", () => {
  it("records first sight of a key at the stream time it was seen", () => {
    const a = trackArrivals({}, ["note:1"], 5000, false);
    expect(a["note:1"]).toEqual({ at: 5000, initial: false, wall: expect.any(Number) });
  });

  it("never moves a key it has already seen, and returns the same object when nothing is new", () => {
    const a = trackArrivals({}, ["note:1"], 5000, false);
    const b = trackArrivals(a, ["note:1"], 9000, false);
    expect(b).toBe(a);
    expect(trackArrivals(a, ["note:1", "note:2"], 9000, false)["note:1"]).toEqual(a["note:1"]);
  });

  it("marks what was already there at first load as initial", () => {
    const a = trackArrivals({}, ["note:1"], 5000, true);
    expect(a["note:1"]).toEqual({ at: 5000, initial: true, wall: expect.any(Number) });
  });
});

describe("copilotInsights", () => {
  it("lists actions, decisions, notes and related, in that order, with design glyphs", () => {
    const list = copilotInsights(
      copilot({
        notes: [{ id: "n1", text: "No date yet." }],
        actions: [{ id: "a1", text: "Send the inventory.", owner: "Me", due: "Fri 16 Oct" }],
        decisions: [{ id: "d1", text: "Pilot on k3s2." }],
        related: [{ path: "10/DGX.md", title: "DGX", snippet: "s", uri: "obsidian://open?x" }],
      }),
    );
    expect(list.map((i) => [i.kind, i.key])).toEqual([
      ["action", "action:a1"],
      ["decision", "decision:d1"],
      ["note", "note:n1"],
      ["related", "related:10/DGX.md"],
    ]);
  });

  it("shows an em dash for a missing owner or due date", () => {
    const [a, b] = copilotInsights(
      copilot({
        actions: [
          { id: "a1", text: "x", owner: "Vendor", due: null },
          { id: "a2", text: "y" },
        ],
      }),
    );
    expect(a!.meta).toBe("Vendor · —");
    expect(b!.meta).toBe("— · —");
  });
});

describe("buildFeed", () => {
  const insight = (key: string, kind: Insight["kind"] = "note"): Insight => ({ key, kind, text: key });

  it("places an insight where the copilot found it, after the line that was latest then", () => {
    const lines = [line("l1", 1000), line("l2", 3000), line("l3", 6000)];
    const arrivals = trackArrivals({}, ["note:n"], 3000, false);
    const feed = buildFeed(lines, [], [insight("note:n")], arrivals);
    expect(feed.map((e) => e.key)).toEqual(["l1", "l2", "note:n", "l3"]);
  });

  it("puts markers at their own time among the lines", () => {
    const marker: Marker = { t_ms: 2000, label: "Pricing" };
    const feed = buildFeed([line("l1", 1000), line("l2", 3000)], [marker], [], {});
    expect(feed.map((e) => e.kind)).toEqual(["line", "marker", "line"]);
  });

  it("keeps insights that arrived together in section order: actions, decisions, notes, related", () => {
    const list = [insight("note:1", "note"), insight("action:1", "action"), insight("decision:1", "decision")];
    const arrivals = trackArrivals({}, list.map((i) => i.key), 1000, false);
    const feed = buildFeed([line("l1", 500)], [], list, arrivals);
    expect(feed.map((e) => e.key)).toEqual(["l1", "action:1", "decision:1", "note:1"]);
  });

  it("never puts a work item before the line it came from, even if it was first seen early", () => {
    const work: Insight = { key: "work:w", kind: "work", text: "Add ARM", srcMs: 4000 };
    const arrivals = trackArrivals({}, ["work:w"], 0, true);
    const feed = buildFeed([line("l1", 1000), line("l2", 4000), line("l3", 9000)], [], [work], arrivals);
    expect(feed.map((e) => e.key)).toEqual(["l1", "l2", "work:w", "l3"]);
  });

  it("an insight with no recorded arrival sits at the end rather than vanishing", () => {
    const feed = buildFeed([line("l1", 1000)], [], [insight("note:z")], {});
    expect(feed.map((e) => e.key)).toEqual(["l1", "note:z"]);
  });
});

describe("markNew", () => {
  const list = [
    { key: "action:1", kind: "action", text: "a" },
    { key: "decision:1", kind: "decision", text: "d" },
  ] as Insight[];

  it("tints only what arrived after you last looked, never what was there at load", () => {
    const arrivals = {
      "action:1": { at: 1000, initial: true, wall: 1000 },
      "decision:1": { at: 5000, initial: false, wall: 5000 },
    };
    const feed = buildFeed([line("l1", 500), line("l2", 6000)], [], list, arrivals);
    const m = markNew(feed, arrivals, 2000);
    expect(m.count).toBe(1);
    expect(m.firstNewKey).toBe("decision:1");
    expect(m.newKeys.has("decision:1")).toBe(true);
    expect(m.newKeys.has("action:1")).toBe(false);
  });

  it("is empty when nothing is new", () => {
    const m = markNew(buildFeed([], [], list, {}), {}, 0);
    expect(m).toEqual({ count: 0, firstNewKey: null, newKeys: new Set() });
  });
});

describe("filters", () => {
  const list: Insight[] = [
    { key: "action:1", kind: "action", text: "a" },
    { key: "action:2", kind: "action", text: "b" },
    { key: "decision:1", kind: "decision", text: "d" },
    { key: "note:1", kind: "note", text: "n" },
    { key: "work:1", kind: "work", text: "w" },
  ];

  it("counts per kind", () => {
    expect(filterCounts(list)).toEqual({ action: 2, decision: 1, note: 1, related: 0, work: 1 });
  });

  it("groups by section in the design's order, dropping empty sections", () => {
    expect(groupInsights(list, "all").map((g) => [g.kind, g.items.length])).toEqual([
      ["action", 2],
      ["decision", 1],
      ["note", 1],
      ["work", 1],
    ]);
    expect(groupInsights(list, "decision").map((g) => g.kind)).toEqual(["decision"]);
  });
});

describe("workInsights", () => {
  it("lists work items with their state and first source", () => {
    const [w] = workInsights([
      {
        id: "w1",
        state: "draft",
        title: "Add ARM runner",
        repo: "o/r",
        sources: [{ segment_id: "s", start_ms: 872000, text: "ARM?" }],
      } as never,
    ]);
    expect(w).toMatchObject({ key: "work:w1", kind: "work", text: "Add ARM runner", srcMs: 872000, tag: "Draft" });
  });
});
