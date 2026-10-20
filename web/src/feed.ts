import type { Line } from "./reducer";
import type { Copilot, Marker, WorkItem } from "./types";
import { STATE_LABEL } from "./workitems";

/** The kinds of copilot finding, in the order every list shows them. */
export const SECTION_ORDER = ["action", "decision", "note", "related", "work"] as const;
export type InsightKind = (typeof SECTION_ORDER)[number];

export const GLYPH: Record<InsightKind, string> = {
  action: "☐",
  decision: "✓",
  note: "•",
  related: "▶",
  work: "▶",
};
export const KIND_LABEL: Record<InsightKind, string> = {
  action: "Action",
  decision: "Decision",
  note: "Note",
  related: "Related",
  work: "Work",
};
export const SECTION_LABEL: Record<InsightKind, string> = {
  action: "Actions",
  decision: "Decisions",
  note: "Notes",
  related: "Related",
  work: "Work items",
};

/** One copilot finding (or work item) as the feed and the grouped lists show it. */
export interface Insight {
  key: string;
  kind: InsightKind;
  text: string;
  /** "Me · Fri 16 Oct", a vault path, a repository. */
  meta?: string;
  /** Opens the note in Obsidian; related items only. */
  href?: string;
  /** Where in the stream it was said (work items), for a ▶ link. */
  srcMs?: number;
  srcText?: string;
  /** A short state tag ("Draft"). */
  tag?: string;
  /** Related notes: the first lines of the note. */
  snippet?: string;
}

export function copilotInsights(c: Copilot): Insight[] {
  return [
    ...c.actions.map((a) => ({
      key: `action:${a.id}`,
      kind: "action" as const,
      text: a.text,
      meta: `${a.owner || "—"} · ${a.due || "—"}`,
    })),
    ...c.decisions.map((d) => ({ key: `decision:${d.id}`, kind: "decision" as const, text: d.text })),
    ...c.notes.map((n) => ({ key: `note:${n.id}`, kind: "note" as const, text: n.text })),
    ...c.related.map((r) => ({
      key: `related:${r.path}`,
      kind: "related" as const,
      text: r.title,
      meta: r.path,
      snippet: r.snippet,
      href: r.uri.startsWith("obsidian://") ? r.uri : undefined,
    })),
  ];
}

export function workInsights(items: WorkItem[]): Insight[] {
  return items.map((w) => ({
    key: `work:${w.id}`,
    kind: "work" as const,
    text: w.title,
    meta: w.repo ?? undefined,
    srcMs: w.sources[0]?.start_ms,
    srcText: w.sources[0]?.text,
    tag: STATE_LABEL[w.state],
  }));
}

/**
 * When a key was first seen: the stream time then (its place in the feed and its age), the wall
 * clock then (to compare with when you last looked), and whether it was already there at load.
 */
export type Arrivals = Record<string, { at: number; initial: boolean; wall: number }>;

/**
 * Record the keys not seen before. Returns `prev` itself when nothing is new, so it can be
 * used as a cheap change check. Items present at first load are `initial`: they are placed
 * but never called new.
 */
export function trackArrivals(
  prev: Arrivals,
  keys: string[],
  streamMs: number,
  initial: boolean,
  wall: number = Date.now(),
): Arrivals {
  const fresh = keys.filter((k) => !(k in prev));
  if (fresh.length === 0) return prev;
  const next = { ...prev };
  for (const k of fresh) next[k] = { at: streamMs, initial, wall };
  return next;
}

export type FeedEntry =
  | { kind: "line"; key: string; sortMs: number; line: Line }
  | { kind: "marker"; key: string; sortMs: number; marker: Marker }
  | { kind: "insight"; key: string; sortMs: number; insight: Insight };

/**
 * One timeline: speech and markers at their stream time, findings where the copilot found them
 * ("placed when found, never at the source time", so nothing is inserted above the reader).
 */
export function buildFeed(lines: Line[], markers: Marker[], insights: Insight[], arrivals: Arrivals): FeedEntry[] {
  const entries: FeedEntry[] = [
    ...lines.map((line) => ({ kind: "line" as const, key: line.key, sortMs: line.sortMs, line })),
    ...markers.map((marker) => ({
      kind: "marker" as const,
      key: `marker:${marker.t_ms}:${marker.label}`,
      sortMs: marker.t_ms,
      marker,
    })),
  ];
  const lastMs = entries.reduce((m, e) => Math.max(m, e.sortMs), 0);
  const order = (i: Insight) => SECTION_ORDER.indexOf(i.kind);
  const sorted = [...insights].sort((a, b) => order(a) - order(b));
  for (const insight of sorted) {
    const seen = arrivals[insight.key]?.at;
    // A work item cannot come before the line it was drafted from.
    const at = seen === undefined ? undefined : Math.max(seen, insight.srcMs ?? 0);
    // Just after anything at the same stream time; no record sorts to the end.
    const sortMs = at === undefined ? lastMs + 0.5 : at + 0.5;
    entries.push({ kind: "insight", key: insight.key, sortMs, insight });
  }
  // Array.prototype.sort is stable, so ties keep the order built above.
  return entries.sort((a, b) => a.sortMs - b.sortMs);
}

export interface NewMarks {
  count: number;
  firstNewKey: string | null;
  newKeys: Set<string>;
}

/** Which findings arrived after you last looked (wall clock) and were not there at load. */
export function markNew(feed: FeedEntry[], arrivals: Arrivals, lookedWall: number): NewMarks {
  const newKeys = new Set<string>();
  let firstNewKey: string | null = null;
  for (const e of feed) {
    if (e.kind !== "insight") continue;
    const a = arrivals[e.key];
    if (a && !a.initial && a.wall > lookedWall) {
      newKeys.add(e.key);
      firstNewKey ??= e.key;
    }
  }
  return { count: newKeys.size, firstNewKey, newKeys };
}

export type Filter = "all" | InsightKind;

export function filterCounts(insights: Insight[]): Record<InsightKind, number> {
  const counts = { action: 0, decision: 0, note: 0, related: 0, work: 0 };
  for (const i of insights) counts[i.kind] += 1;
  return counts;
}

/** Findings grouped by section, in the design's order, for a chosen filter. */
export function groupInsights(insights: Insight[], filter: Filter): { kind: InsightKind; items: Insight[] }[] {
  return SECTION_ORDER.filter((k) => filter === "all" || filter === k)
    .map((kind) => ({ kind, items: insights.filter((i) => i.kind === kind) }))
    .filter((g) => g.items.length > 0);
}
