// Wire types for protocol v1 (see protocol/v1/README.md) and the REST read API.

export type Channel = "mic" | "system";
export type Speaker = "me" | "them";
export type SuggestionKind = "question" | "gap" | "counterpoint" | "fact_check";
export type SessionStatus = "live" | "finalizing" | "done" | "failed";

export interface Segment {
  id: string;
  channel: Channel;
  speaker: Speaker;
  start_ms: number;
  end_ms: number;
  text: string;
  stt_tier: string | null;
}

export interface Note {
  id: string;
  text: string;
}
export interface Action {
  id: string;
  text: string;
  owner?: string | null;
  due?: string | null;
}
export interface Suggestion {
  id: string;
  kind: SuggestionKind;
  text: string;
  expires_at_ms: number;
}
export interface Related {
  path: string;
  title: string;
  snippet: string;
  uri: string;
}

export interface Copilot {
  version: number;
  notes: Note[];
  actions: Action[];
  decisions: Note[];
  suggestions: Suggestion[];
  related: Related[];
}

export interface Marker {
  t_ms: number;
  label: string;
}

export interface Status {
  stt_tier: string | null;
  llm_ok: boolean;
  lag_ms: number;
}

export interface FinalNoteRef {
  path: string;
  title: string;
}

// --- messages seen by a viewer (server -> client, plus republished producer messages) ---

export interface HelloAckMsg {
  type: "hello_ack";
  session: string;
  acked: Record<Channel, number | null>;
}
export interface AckMsg {
  type: "ack";
  channel: Channel;
  seq: number;
}
export interface SegmentMsg extends Segment {
  type: "segment";
}
export interface CopilotMsg extends Copilot {
  type: "copilot";
}
export interface StatusMsg extends Status {
  type: "status";
}
export interface FinalNoteMsg extends FinalNoteRef {
  type: "final_note";
}
export interface ErrorMsg {
  type: "error";
  code: string;
  message: string;
}
export interface DraftSegmentMsg {
  type: "draft_segment";
  channel: Channel;
  start_ms: number;
  end_ms: number;
  text: string;
  final: boolean;
}
export interface MarkerMsg extends Marker {
  type: "marker";
}

export type ServerEvent =
  | HelloAckMsg
  | AckMsg
  | SegmentMsg
  | CopilotMsg
  | StatusMsg
  | FinalNoteMsg
  | ErrorMsg
  | DraftSegmentMsg
  | MarkerMsg;

// --- REST ---

export interface SessionSummary {
  id: string;
  title: string | null;
  mode: "meeting" | "solo";
  status: SessionStatus;
  channels: Channel[];
  started_at: string;
  ended_at: string | null;
  local_only: boolean;
}

export interface SessionPage {
  items: SessionSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface FinalNoteDoc extends FinalNoteRef {
  markdown: string | null;
}

export interface SessionDetail extends SessionSummary {
  segments: Segment[];
  copilot: Copilot;
  markers: Marker[];
  final_note: FinalNoteDoc | null;
}
