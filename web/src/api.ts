import type {
  ActionEvent,
  ActionItem,
  ActionPage,
  ApproveResult,
  AdminUser,
  Health,
  Channel,
  FamilyApp,
  Device,
  Me,
  NewDevice,
  Publication,
  PublishingQueue,
  Processing,
  SessionDetail,
  SessionPage,
  SessionSummary,
  WorkItem,
  WorkItemEdit,
  WorkItemPage,
  WorkItemPrompt,
  WorkItemState,
} from "./types";

// The browser is authenticated by the server's HttpOnly session cookie (set by /auth/login),
// so nothing here holds a credential. Same-origin fetches add the Origin header the server
// checks on writes.

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let unauthorized: () => void = () => undefined;

/** Called on every 401: the session cookie is missing or expired. */
export function onUnauthorized(fn: () => void): void {
  unauthorized = fn;
}

async function call(path: string, init: RequestInit, fetchImpl: typeof fetch): Promise<Response> {
  const res = await fetchImpl(path, {
    ...init,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers as Record<string, string> | undefined),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 401) unauthorized();
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  return res;
}

async function request<T>(path: string, init: RequestInit = {}, f: typeof fetch = fetch): Promise<T> {
  return (await (await call(path, init, f)).json()) as T;
}

async function send(path: string, init: RequestInit, f: typeof fetch = fetch): Promise<void> {
  await call(path, init, f);
}

const sid = (id: string) => `/v1/sessions/${encodeURIComponent(id)}`;

export interface ListOptions {
  limit?: number;
  offset?: number;
  /** Admins only: everyone's sessions. */
  all?: boolean;
  /** Admins only: one user's sessions. */
  userId?: string;
}

export function sessionsPath({ limit = 100, offset = 0, all, userId }: ListOptions = {}): string {
  const q = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (userId) q.set("user_id", userId);
  else if (all) q.set("all", "true");
  return `/v1/sessions?${q}`;
}

export const me = (f?: typeof fetch) => request<Me>("/v1/me", {}, f);

/** The family's apps for the ▦ switcher; empty means no switcher. */
export const familyApps = (f?: typeof fetch) =>
  request<{ apps: FamilyApp[] }>("/v1/apps", {}, f).then((r) => r.apps);

export const health = (f?: typeof fetch) => request<Health>("/healthz", {}, f);

/** End the browser session; the server clears the cookie. */
export const logout = (f?: typeof fetch) => send("/auth/logout", { method: "POST" }, f);

/** Where the browser goes to sign in; Pocket-ID sends it back to `returnTo`. */
export const loginUrl = (returnTo: string) =>
  `/auth/login?client=web&return_to=${encodeURIComponent(returnTo)}`;

export const listSessions = (opts: ListOptions = {}, f?: typeof fetch) =>
  request<SessionPage>(sessionsPath(opts), {}, f);

export const getSession = (id: string, f?: typeof fetch) => request<SessionDetail>(sid(id), {}, f);

export const viewerTicket = async (id: string, f?: typeof fetch) =>
  (
    await request<{ ticket: string; expires_in: number }>(
      `${sid(id)}/ticket`,
      { method: "POST", body: JSON.stringify({ role: "viewer" }) },
      f,
    )
  ).ticket;

export interface UploadOptions {
  mode: "meeting" | "solo";
  /** Blank: the server names the session after the file. */
  title?: string;
}

/** Upload a recorded file (any audio format); the server transcribes it as a new session. */
export async function uploadRecording(
  file: File,
  { mode, title }: UploadOptions,
  f?: typeof fetch,
): Promise<string> {
  const q = new URLSearchParams({ filename: file.name, mode });
  if (title?.trim()) q.set("title", title.trim());
  const init: RequestInit = {
    method: "POST",
    body: file,
    headers: { "Content-Type": file.type || "application/octet-stream" },
  };
  return (await request<{ id: string }>(`/v1/sessions/upload?${q}`, init, f)).id;
}

/** Set a session's title; a blank title clears it. */
export const renameSession = (id: string, title: string, f?: typeof fetch) =>
  request<SessionSummary>(sid(id), { method: "PATCH", body: JSON.stringify({ title }) }, f);

/** A channel's recording as WAV; the cookie authenticates `<audio src>` too. */
export const audioUrl = (id: string, channel: Channel) => `${sid(id)}/audio/${channel}.wav`;

export const listDevices = (f?: typeof fetch) => request<Device[]>("/v1/devices", {}, f);

export const createDevice = (name: string, f?: typeof fetch) =>
  request<NewDevice>("/v1/devices", { method: "POST", body: JSON.stringify({ name }) }, f);

export const revokeDevice = (id: string, f?: typeof fetch) =>
  send(`/v1/devices/${encodeURIComponent(id)}`, { method: "DELETE" }, f);

export const listUsers = (f?: typeof fetch) => request<AdminUser[]>("/v1/admin/users", {}, f);

/** Replace the note's Markdown; an open pull request gets the edit too. */
export const editNote = (id: string, markdown: string, f?: typeof fetch) =>
  request<SessionDetail>(`${sid(id)}/note`, { method: "PUT", body: JSON.stringify({ markdown }) }, f);

/** Write the note again from the transcript; the session is `finalizing` meanwhile. */
/** Stop recording and write the note. Owner only; returns the updated session. */
export const endSession = (id: string, f?: typeof fetch) =>
  request<SessionSummary>(`${sid(id)}/end`, { method: "POST" }, f);

export const regenerateNote = (id: string, f?: typeof fetch) =>
  request<SessionDetail>(`${sid(id)}/regenerate`, { method: "POST" }, f);

/**
 * Transcribe the recording again, then write the note again. Never starts a second job: with one
 * running, `started` is false and `processing` is that job.
 */
export const processSession = (id: string, f?: typeof fetch) =>
  request<{ started: boolean; processing: Processing }>(`${sid(id)}/process`, { method: "POST" }, f);

/** Just the processing state, to poll while a job runs. */
export const getProcessing = (id: string, f?: typeof fetch) => request<Processing>(`${sid(id)}/processing`, {}, f);

/** The publication, after the server checked an open PR on GitHub. */
export const getPublication = (id: string, f?: typeof fetch) =>
  request<{ publication: Publication | null }>(`${sid(id)}/publication`, {}, f);

export type PublishAction = "publish" | "merge" | "close" | "skip" | "unskip";

export function publicationRequest(id: string, action: PublishAction): { path: string; method: string } {
  const base = `${sid(id)}/publication`;
  switch (action) {
    case "publish":
      return { path: base, method: "POST" };
    case "skip":
      return { path: `${base}/skip`, method: "POST" };
    case "unskip":
      return { path: `${base}/skip`, method: "DELETE" };
    default:
      return { path: `${base}/${action}`, method: "POST" };
  }
}

/** Publish (open the PR), merge, close, skip or unskip; returns the updated session. */
export const publicationAction = (id: string, action: PublishAction, f?: typeof fetch) => {
  const { path, method } = publicationRequest(id, action);
  return request<SessionDetail>(path, { method }, f);
};

export const getPublishing = (f?: typeof fetch) => request<PublishingQueue>("/v1/publishing", {}, f);

/** Check every open PR on GitHub now. */
export const syncPublishing = (f?: typeof fetch) =>
  request<PublishingQueue>("/v1/publishing/sync", { method: "POST" }, f);

/** Publish every ready note in the background; poll `getPublishing` for progress. */
export const publishAll = (f?: typeof fetch) =>
  request<{ status: "started" | "running" }>("/v1/publishing/publish-all", { method: "POST" }, f);

export const setAutoPublish = (on: boolean, f?: typeof fetch) =>
  request<Me>("/v1/me", { method: "PATCH", body: JSON.stringify({ auto_publish: on }) }, f);

// --- Work items ---

const wi = (id: string) => `/v1/work-items/${encodeURIComponent(id)}`;

/** A session's work items, plus the repos they may target. */
export const sessionWorkItems = (id: string, f?: typeof fetch) =>
  request<WorkItemPage>(`${sid(id)}/work-items`, {}, f);

/** The caller's work items across sessions (default: drafts and approved). */
export function myWorkItems(states: WorkItemState[] = [], f?: typeof fetch) {
  const q = new URLSearchParams();
  for (const s of states) q.append("state", s);
  const qs = q.toString();
  return request<WorkItemPage>(`/v1/work-items${qs ? `?${qs}` : ""}`, {}, f);
}

/** Edit fields; they are then locked against the copilot. An approved item goes back to draft. */
export const editWorkItem = (id: string, fields: WorkItemEdit, f?: typeof fetch) =>
  request<WorkItem>(wi(id), { method: "PATCH", body: JSON.stringify(fields) }, f);

export type WorkItemAction = "approve" | "unapprove" | "discard" | "restore" | "done" | "reopen";

export const workItemAction = (id: string, action: WorkItemAction, f?: typeof fetch) =>
  request<WorkItem>(`${wi(id)}/${action}`, { method: "POST" }, f);

/** Fold `sources` (drafts of the same session) into `into`. */
export const mergeWorkItems = (into: string, sources: string[], f?: typeof fetch) =>
  request<WorkItem>(
    "/v1/work-items/merge",
    { method: "POST", body: JSON.stringify({ into, sources }) },
    f,
  );

export const workItemPrompt = (id: string, f?: typeof fetch) =>
  request<WorkItemPrompt>(`${wi(id)}/prompt`, {}, f);

/** Record the copy in the audit log. */
export const workItemCopied = (id: string, f?: typeof fetch) =>
  send(`${wi(id)}/copied`, { method: "POST" }, f);

/** Run an approved item as headless Claude Code (a draft PR comes back). */
export const dispatchWorkItem = (id: string, f?: typeof fetch) =>
  request<WorkItem>(`${wi(id)}/dispatch`, { method: "POST" }, f);

/** Reply to the item's last run: a follow-up run continues its branch and draft PR. */
export const continueDispatch = (id: string, guidance: string, f?: typeof fetch) =>
  request<WorkItem>(
    `${wi(id)}/dispatch/continue`,
    { method: "POST", body: JSON.stringify({ guidance }) },
    f,
  );

/** Stop the item's queued or running run. */
export const cancelDispatch = (id: string, f?: typeof fetch) =>
  request<WorkItem>(`${wi(id)}/dispatch/cancel`, { method: "POST" }, f);

// --- Action proposals ---

const act = (id: string) => `/v1/actions/${encodeURIComponent(id)}`;

/** A session's proposed actions and what became of them. */
export const sessionActions = (id: string, f?: typeof fetch) =>
  request<ActionPage>(`${sid(id)}/actions`, {}, f);

/** Approve actions, each bound to the `payload_sha256` the reviewer was shown. They run in order. */
export const approveActions = (
  sessionId: string,
  items: { id: string; payload_sha256: string }[],
  f?: typeof fetch,
) =>
  request<ApproveResult>(
    `${sid(sessionId)}/actions/approve`,
    { method: "POST", body: JSON.stringify({ items }) },
    f,
  );

export const editAction = (id: string, changes: Record<string, string | null>, f?: typeof fetch) =>
  request<ActionItem>(act(id), { method: "PATCH", body: JSON.stringify({ changes }) }, f);

export const rejectAction = (id: string, f?: typeof fetch) =>
  request<ActionItem>(`${act(id)}/reject`, { method: "POST" }, f);

/** Run a failed action again: the owner's explicit request, never automatic. */
export const retryAction = (id: string, f?: typeof fetch) =>
  request<ActionItem>(`${act(id)}/retry`, { method: "POST" }, f);

export const actionEvents = (id: string, f?: typeof fetch) =>
  request<{ events: ActionEvent[] }>(`${act(id)}/events`, {}, f);
