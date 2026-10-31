import type { Arrivals } from "../feed";
import type { Suggestion, SuggestionKind } from "../types";

export type DockState = "new" | "ageing" | "expired" | "dismissed";

export interface DockItem {
  id: string;
  kind: SuggestionKind;
  text: string;
  state: DockState;
  /** How long ago it arrived, in stream ms. */
  ageMs: number;
}

/** What the viewer did or saw at a given copilot version: held in place until the next one. */
export type Holds = Record<string, { kind: "expired" | "dismissed"; version: number }>;

export interface DockInput {
  suggestions: Suggestion[];
  /** Dismissed in this browser (persisted). */
  dismissed: Set<string>;
  holds: Holds;
  /** The copilot snapshot version now. */
  version: number;
  streamMs: number;
  arrivals: Arrivals;
  /** Wall clock of your last look. */
  lookedAt: number;
}

export interface DockView {
  lead: DockItem | null;
  /** At most one older line. */
  more: DockItem[];
}

const MORE = 1;

/**
 * What the Now dock shows. Newest first. A dismissed or expired suggestion keeps its place,
 * quiet, until the copilot sends its next update; one dismissed on an earlier visit stays gone.
 */
export function dockView(i: DockInput): DockView {
  const items: (DockItem & { at: number; wall: number })[] = [];
  for (const s of i.suggestions) {
    const arrival = i.arrivals[`suggestion:${s.id}`];
    const at = arrival?.at ?? i.streamMs;
    const hold = i.holds[s.id];
    const held = hold && hold.version === i.version ? hold.kind : null;
    const isDismissed = i.dismissed.has(s.id);
    const isExpired = s.expires_at_ms <= i.streamMs;
    let state: DockState;
    if (isDismissed) {
      if (held !== "dismissed") continue;
      state = "dismissed";
    } else if (isExpired) {
      if (held !== "expired") continue;
      state = "expired";
    } else {
      state = arrival && !arrival.initial && arrival.wall > i.lookedAt ? "new" : "ageing";
    }
    items.push({ id: s.id, kind: s.kind, text: s.text, state, ageMs: Math.max(0, i.streamMs - at), at, wall: arrival?.wall ?? 0 });
  }
  // Newest first; a live suggestion leads a quiet one that was dismissed or has expired.
  const rank = (x: DockItem) => (x.state === "dismissed" || x.state === "expired" ? 1 : 0);
  items.sort((a, b) => rank(a) - rank(b) || b.at - a.at || b.wall - a.wall);
  const strip = ({ at: _at, wall: _wall, ...rest }: DockItem & { at: number; wall: number }): DockItem => rest;
  const [lead, ...rest] = items;
  return { lead: lead ? strip(lead) : null, more: rest.slice(0, MORE).map(strip) };
}
