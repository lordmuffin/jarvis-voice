import type {
  AdminUser,
  Health,
  Channel,
  Device,
  Me,
  NewDevice,
  Publication,
  PublishingQueue,
  SessionDetail,
  SessionPage,
  SessionSummary,
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
export const regenerateNote = (id: string, f?: typeof fetch) =>
  request<SessionDetail>(`${sid(id)}/regenerate`, { method: "POST" }, f);

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
