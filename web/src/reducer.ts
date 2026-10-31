import type {
  Channel,
  Copilot,
  DraftSegmentMsg,
  FinalNoteRef,
  Marker,
  Segment,
  ServerEvent,
  SessionDetail,
  SessionSummary,
  SpeakerInfo,
  SpeakerUpdateMsg,
  Status,
} from "./types";
import { whoIs, type Who } from "./speakers";

/** A client-side draft transcript line (not authoritative; replaced by a final segment). */
export interface Draft {
  channel: Channel;
  start_ms: number;
  end_ms: number;
  text: string;
  final: boolean;
}

export type ConnectionState = "idle" | "connecting" | "open" | "closed";

export interface LiveState {
  /** Authoritative segments, ordered by start_ms. */
  segments: Segment[];
  /** Latest draft per overlapping time range and channel. */
  drafts: Draft[];
  markers: Marker[];
  copilot: Copilot;
  status: Status | null;
  finalNote: FinalNoteRef | null;
  error: { code: string; message: string } | null;
  acked: Record<Channel, number | null>;
  connection: ConnectionState;
  /** Furthest stream time seen (ms); the clock suggestions expire against. */
  streamMs: number;
  /** The session row (title, status, streaming) as of the latest REST snapshot or rename. */
  session: SessionSummary | null;
  /**
   * A final segment that replaced a draft keeps the draft's row: its render key and the time it
   * sorts by. Keyed by segment id; a segment with no draft is not listed (it is its own row).
   */
  slots: Record<string, { key: string; sortMs: number }>;
  /** Voices heard so far, by id (from `speaker_update`). */
  speakers: Record<string, SpeakerInfo>;
  /** Who spoke each segment, by segment id: the highest rev seen, even before its segment. */
  assignments: Record<string, { speaker_id: string | null; rev: number; mixed: boolean }>;
}

/** Events the reducer folds: every server message plus local lifecycle/bootstrap events. */
export type Event =
  | ServerEvent
  | { type: "snapshot"; detail: SessionDetail }
  | { type: "session"; session: SessionSummary }
  | { type: "connection"; state: ConnectionState };

export const EMPTY_COPILOT: Copilot = {
  version: -1,
  notes: [],
  actions: [],
  decisions: [],
  suggestions: [],
  related: [],
};

export const initialState: LiveState = {
  segments: [],
  drafts: [],
  markers: [],
  copilot: EMPTY_COPILOT,
  status: null,
  finalNote: null,
  error: null,
  acked: { mic: null, system: null },
  connection: "idle",
  streamMs: 0,
  session: null,
  slots: {},
  speakers: {},
  assignments: {},
};

const overlaps = (a: { start_ms: number; end_ms: number }, b: { start_ms: number; end_ms: number }) =>
  a.start_ms < b.end_ms && b.start_ms < a.end_ms;

const byStart = <T extends { start_ms: number }>(a: T, b: T) => a.start_ms - b.start_ms;

/** Insert/replace by id, keeping start_ms order. Returns the same array if nothing changed. */
function upsertSegment(list: Segment[], seg: Segment): Segment[] {
  const i = list.findIndex((s) => s.id === seg.id);
  if (i >= 0) {
    const cur = list[i]!;
    if (JSON.stringify(cur) === JSON.stringify(seg)) return list;
    const next = list.slice();
    next[i] = seg;
    return next.sort(byStart);
  }
  return [...list, seg].sort(byStart);
}

function applyDraft(drafts: Draft[], msg: DraftSegmentMsg): Draft[] {
  const incoming: Draft = {
    channel: msg.channel,
    start_ms: msg.start_ms,
    end_ms: msg.end_ms,
    text: msg.text,
    final: msg.final,
  };
  // A draft supersedes earlier drafts of the same channel it overlaps (they grow in place).
  const kept = drafts.filter((d) => d.channel !== incoming.channel || !overlaps(d, incoming));
  return [...kept, incoming].sort(byStart);
}

export const draftKey = (d: Pick<Draft, "channel" | "start_ms">) => `draft-${d.channel}-${d.start_ms}`;

function mergeSegments(state: LiveState, incoming: Segment[]): LiveState {
  let segments = state.segments;
  for (const s of incoming) segments = upsertSegment(segments, s);
  if (segments === state.segments) return state;
  // Final segments replace drafts they overlap on the same channel, and take over their row.
  const slots = { ...state.slots };
  for (const s of incoming) {
    if (slots[s.id]) continue;
    const replaced = state.drafts.find((d) => d.channel === s.channel && overlaps(s, d));
    if (replaced) slots[s.id] = { key: draftKey(replaced), sortMs: replaced.start_ms };
  }
  const drafts = state.drafts.filter(
    (d) => !incoming.some((s) => s.channel === d.channel && overlaps(s, d)),
  );
  const streamMs = Math.max(state.streamMs, ...incoming.map((s) => s.end_ms));
  return { ...state, segments, drafts, streamMs, slots };
}

function applyCopilot(state: LiveState, c: Copilot): LiveState {
  // Snapshots are full state; only a strictly higher version wins (reordering/duplicates).
  if (c.version <= state.copilot.version) return state;
  const { version, notes, actions, decisions, suggestions, related } = c;
  return { ...state, copilot: { version, notes, actions, decisions, suggestions, related } };
}

function addMarker(markers: Marker[], m: Marker): Marker[] {
  if (markers.some((x) => x.t_ms === m.t_ms && x.label === m.label)) return markers;
  return [...markers, { t_ms: m.t_ms, label: m.label }].sort((a, b) => a.t_ms - b.t_ms);
}

function summaryOf(s: SessionSummary): SessionSummary {
  const { id, title, mode, status, channels, started_at, ended_at, local_only, streaming, owner } = s;
  return { id, title, mode, status, channels, started_at, ended_at, local_only, streaming, owner };
}

/** Pure: `(state, event) => state`. Unknown events return the state unchanged. */
function applySpeakers(state: LiveState, msg: SpeakerUpdateMsg): LiveState {
  const speakers = { ...state.speakers };
  for (const sp of msg.speakers) speakers[sp.id] = sp;
  const assignments = { ...state.assignments };
  for (const a of msg.assignments) {
    if (a.rev > (assignments[a.segment_id]?.rev ?? -1)) {
      assignments[a.segment_id] = { speaker_id: a.speaker_id, rev: a.rev, mixed: a.mixed ?? false };
    }
  }
  return { ...state, speakers, assignments };
}

export function reduce(state: LiveState, event: Event): LiveState {
  switch (event.type) {
    case "hello_ack":
      return { ...state, acked: { ...event.acked } };
    case "ack": {
      const cur = state.acked[event.channel];
      if (cur !== null && cur >= event.seq) return state;
      return { ...state, acked: { ...state.acked, [event.channel]: event.seq } };
    }
    case "segment": {
      const { type: _t, ...seg } = event;
      return mergeSegments(state, [seg]);
    }
    case "speaker_update":
      return applySpeakers(state, event);
    case "draft_segment":
      return {
        ...state,
        drafts: applyDraft(state.drafts, event),
        streamMs: Math.max(state.streamMs, event.end_ms),
      };
    case "marker":
      return { ...state, markers: addMarker(state.markers, event) };
    case "copilot":
      return applyCopilot(state, event);
    case "status": {
      const { stt_tier, llm_ok, lag_ms } = event;
      return { ...state, status: { stt_tier, llm_ok, lag_ms } };
    }
    case "final_note":
      return { ...state, finalNote: { path: event.path, title: event.title } };
    case "error":
      return { ...state, error: { code: event.code, message: event.message } };
    case "connection":
      return { ...state, connection: event.state };
    case "session":
      return { ...state, session: summaryOf(event.session) };
    case "snapshot": {
      const d = event.detail;
      let next = mergeSegments(state, d.segments);
      next = applySpeakers(next, {
        type: "speaker_update",
        speakers: d.speakers ?? [],
        assignments: d.segments
          .filter((s) => s.speaker_id)
          .map((s) => ({
            segment_id: s.id,
            speaker_id: s.speaker_id ?? null,
            rev: s.speaker_rev ?? 0,
            final: (s.speaker_rev ?? 0) >= 2,
            mixed: s.mixed,
          })),
      });
      next = applyCopilot(next, d.copilot);
      let markers = next.markers;
      for (const m of d.markers) markers = addMarker(markers, m);
      const finalNote = d.final_note
        ? { path: d.final_note.path, title: d.final_note.title }
        : next.finalNote;
      return { ...next, markers, finalNote, session: summaryOf(d) };
    }
    default:
      return state;
  }
}

/**
 * Transcript lines for display: authoritative segments plus the drafts not yet replaced. `key`
 * is the row's identity (a final reuses its draft's), `sortMs` where it sits in the order.
 */
export type Line = {
  kind: "segment" | "draft";
  key: string;
  sortMs: number;
  start_ms: number;
  end_ms: number;
  speaker: "me" | "them";
  text: string;
  id: string;
  /** Who, once voices were told apart; absent for a draft or an unidentified line. */
  who?: Who;
  speaker_id?: string | null;
  /** The line spans a change of speaker. */
  mixed?: boolean;
};

export function transcriptLines(
  state: Pick<LiveState, "segments" | "drafts"> & Partial<Pick<LiveState, "slots" | "speakers" | "assignments">>,
): Line[] {
  const slots = state.slots ?? {};
  const speakers = state.speakers ?? {};
  const assignments = state.assignments ?? {};
  const heard: Record<"me" | "them", Set<string>> = { me: new Set(), them: new Set() };
  for (const s of state.segments) {
    const id = assignments[s.id]?.speaker_id;
    if (id) heard[s.speaker].add(id);
  }
  const voices = { me: heard.me.size, them: heard.them.size };
  const lines: Line[] = state.segments.map((s) => {
    const a = assignments[s.id];
    return {
      kind: "segment",
      id: s.id,
      key: slots[s.id]?.key ?? s.id,
      sortMs: slots[s.id]?.sortMs ?? s.start_ms,
      start_ms: s.start_ms,
      end_ms: s.end_ms,
      speaker: s.speaker,
      text: s.text,
      who: whoIs(s.speaker, a?.speaker_id, speakers, voices),
      speaker_id: a?.speaker_id ?? null,
      mixed: a?.mixed ?? false,
    };
  });
  for (const d of state.drafts) {
    lines.push({
      kind: "draft",
      id: draftKey(d),
      key: draftKey(d),
      sortMs: d.start_ms,
      start_ms: d.start_ms,
      end_ms: d.end_ms,
      // The mic channel is the local speaker; system audio is everyone else.
      speaker: d.channel === "mic" ? "me" : "them",
      text: d.text,
    });
  }
  return lines.sort((a, b) => a.sortMs - b.sortMs);
}
