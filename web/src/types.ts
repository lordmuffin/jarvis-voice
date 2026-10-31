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
  /** Who spoke it (an id into the session's speakers), once voices were told apart (REST). */
  speaker_id?: string | null;
  speaker_rev?: number;
  /** The segment spans a change of speaker; `speaker_id` spoke most of it. */
  mixed?: boolean;
}

/** A voice in the session. `label: null` = not named yet: shown as "Speaker N". */
export interface SpeakerInfo {
  id: string;
  label: string | null;
  enrolled: boolean;
  is_owner: boolean;
  /** "Speaker <ordinal>" while unnamed: the server's numbering, the one its notes use. */
  ordinal?: number;
}
/** Who spoke one segment. Kept only if `rev` is higher than the one already held. */
export interface SpeakerAssignment {
  segment_id: string;
  speaker_id: string | null;
  rev: number;
  final: boolean;
  mixed?: boolean;
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
/** Sent only to connections opened with `features=speaker_update`. */
export interface SpeakerUpdateMsg {
  type: "speaker_update";
  speakers: SpeakerInfo[];
  assignments: SpeakerAssignment[];
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
  | SpeakerUpdateMsg
  | CopilotMsg
  | StatusMsg
  | FinalNoteMsg
  | ErrorMsg
  | DraftSegmentMsg
  | MarkerMsg;

// --- REST ---

export interface Owner {
  id: string;
  display_name: string;
}

export interface SessionSummary {
  id: string;
  title: string | null;
  mode: "meeting" | "solo";
  status: SessionStatus;
  channels: Channel[];
  started_at: string;
  ended_at: string | null;
  local_only: boolean;
  /** A work call: its note is filed apart and never published or pushed. */
  restricted: boolean;
  /** Where an uploaded recording was made ("watch"); absent or null when not stated. */
  source?: string | null;
  /** Audio is arriving now. A `live` session that is not streaming lost its recorder. */
  streaming: boolean;
  owner: Owner;
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
  /** The voices heard; absent from servers before speakers existed. */
  speakers?: SpeakerInfo[];
  copilot: Copilot;
  markers: Marker[];
  final_note: FinalNoteDoc | null;
  /** Per channel of the session: whether its recording can be played. */
  audio: Partial<Record<Channel, boolean>>;
  /** The note's way to the vault repo; null until it is published or skipped. */
  publication: Publication | null;
  /** The caller may publish this session's note (owner/admin, own notes, configured). */
  can_publish: boolean;
  /** The note can be written again from the transcript (an LLM is configured). */
  can_regenerate: boolean;
  /** What transcription and the note are doing now; absent from servers before it existed. */
  processing?: Processing;
}

export type ProcessingPhase = "idle" | "queued" | "running" | "failed";

/**
 * A session's transcription and note jobs, as the server sees them in flight: a live or
 * uploaded recording, an ending session, a regeneration, or `POST .../process`.
 */
export interface Processing {
  transcription: ProcessingPhase;
  summary: ProcessingPhase;
  /** When the job in flight began. */
  started_at: string | null;
  job_source: "live" | "upload" | "finalize" | "regenerate" | "reprocess" | null;
  /**
   * How much of a recording of known length is transcribed (0-1): an upload, a reprocess, an
   * ending session. Null for a live stream; absent from servers before it existed.
   */
  progress?: number | null;
}

export type PublicationState = "publishing" | "open" | "merged" | "closed" | "skipped" | "failed";

/** A note's branch and pull request in the vault repo (one per session). */
export interface Publication {
  state: PublicationState;
  repo_path: string | null;
  branch: string | null;
  pr_number: number | null;
  pr_url: string | null;
  last_error: string | null;
  updated_at: string;
  merged_at: string | null;
  /** The note was edited since it was last pushed; null when unknown. */
  changed: boolean | null;
}

export interface PublishingItem {
  session_id: string;
  title: string;
  started_at: string;
  owner: string;
  note_path: string;
  publication: Publication | null;
}

/** `GET /v1/publishing`: what is waiting to be proposed, under review, and done. */
export interface PublishingQueue {
  repo: string;
  base_branch: string;
  auto_publish: boolean;
  publishing_all: boolean;
  ready: PublishingItem[];
  open: PublishingItem[];
  recent: PublishingItem[];
}

/** The signed-in user (`GET /v1/me`). `device_id` is null for a browser. */
/** GET /healthz: which release channel ("live", or e.g. "lab"), version and commit this
 * server is. `commit` is the short sha the image was built from; "" outside an image. */
export interface Health {
  status: string;
  environment?: string;
  version?: string;
  commit?: string;
}

export interface Me {
  id: string;
  email: string | null;
  display_name: string;
  is_admin: boolean;
  device_id: string | null;
  /** May publish notes to the vault repo. */
  can_publish: boolean;
  /** New notes open their pull request without asking. */
  auto_publish: boolean;
  /** The copilot finds work items in this user's sessions. */
  work_items: boolean;
  /** The copilot proposes actions (tasks, reminders…) in this user's sessions, for approval. */
  actions: boolean;
}

export interface Device {
  id: string;
  name: string;
  created_at: string;
  revoked_at: string | null;
  /** The device making the request; never true from the web. */
  current: boolean;
}

/** `POST /v1/devices`: the token is returned this once only. */
export interface NewDevice extends Device {
  token: string;
}

export interface AdminUser {
  id: string;
  email: string | null;
  display_name: string;
  is_admin: boolean;
  created_at: string;
  last_login_at: string | null;
  sessions: number;
}

// --- Work items: the copilot's draft Claude Code prompts ---

export type WorkItemState = "draft" | "approved" | "discarded" | "merged" | "done";

export interface WorkItemSource {
  segment_id: string;
  start_ms: number;
  end_ms: number;
  text: string;
}

export type DispatchState =
  | "queued"
  | "running"
  | "pr_opened"
  | "no_changes"
  | "failed"
  | "rate_limited"
  | "timed_out"
  | "cancelled"
  | "needs_input";

/** An item's latest run as headless Claude Code. */
export interface Dispatch {
  id: string;
  attempt: number;
  state: DispatchState;
  repo: string;
  branch: string;
  pr_url: string | null;
  pr_number: number | null;
  summary: string | null;
  error: string | null;
  /** What the run asked the owner (`needs_input`). */
  questions: string | null;
  /** The owner's reply this follow-up run was given. */
  guidance: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface WorkItem {
  id: string;
  session_id: string;
  ref: string;
  state: WorkItemState;
  title: string;
  repo: string | null;
  intent: string;
  acceptance_criteria: string[];
  done_when: string | null;
  sources: WorkItemSource[];
  /** Fields the owner edited; the copilot no longer changes them. */
  locked_fields: string[];
  merged_into: string | null;
  branch: string;
  prompt_sha256: string | null;
  approved_at: string | null;
  done_at: string | null;
  /** The merged PR that closed it. */
  merged_pr_url: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  /** What stands between a draft and approval; empty when it can be approved. */
  problems: string[];
  /** Set on the cross-session list. */
  session_title?: string | null;
  session_started_at?: string | null;
  dispatch?: Dispatch | null;
}

export interface WorkRepo {
  name: string;
  description: string;
}

export interface WorkItemPage {
  enabled: boolean;
  dispatch_enabled: boolean;
  repos: WorkRepo[];
  items: WorkItem[];
}

export interface WorkItemPrompt {
  markdown: string;
  /** Approved: exactly what is copied (and later dispatched). */
  frozen: boolean;
  sha256: string | null;
}

export interface WorkItemEdit {
  title?: string;
  repo?: string | null;
  intent?: string;
  acceptance_criteria?: string[];
  done_when?: string | null;
}

/** One entry of the ▦ family switcher (`GET /v1/apps`). `url` is already on this server's
 * release channel; null (`available: false`) means the app has no copy there. */
export interface FamilyApp {
  id: string;
  name: string;
  url: string | null;
  available: boolean;
  current: boolean;
  glance_url: string | null;
}

// --- Action proposals ---

export type ActionKind = "task.create" | "vault.append" | "reminder.schedule" | "git.pr_comment";
export type ActionState =
  | "proposed"
  | "approved"
  | "executing"
  | "succeeded"
  | "failed"
  | "rejected"
  | "expired";

export interface ActionEvidence {
  start_ms: number;
  text: string;
}

/** Something the copilot proposes to do. It does nothing until its owner approves it. */
export interface ActionItem {
  id: string;
  session_id: string;
  ref: string;
  position: number;
  kind: ActionKind;
  state: ActionState;
  payload: Record<string, unknown>;
  /** What an approval binds to: the exact content on screen. */
  payload_sha256: string;
  evidence: ActionEvidence[];
  proposed_by: string;
  approved_by: string | null;
  approved_at: string | null;
  result: Record<string, unknown> | null;
  executed_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ActionPage {
  enabled: boolean;
  items: ActionItem[];
}

export type ApprovalOutcome = "approved" | "already" | "stale" | "not_open" | "not_found";

export interface ApproveResult {
  results: { id: string; outcome: ApprovalOutcome }[];
}

export interface ActionEvent {
  at: string;
  actor_user_id: string | null;
  actor_kind: "web" | "device" | "system";
  event: string;
  payload: Record<string, unknown>;
}
