import type { SessionDetail, SessionPage } from "./types";

const TOKEN_KEY = "jarvis-live.device-token";

// localStorage can throw (blocked site data, private windows); the app must still work.
export function loadToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function saveToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* kept in memory only */
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* nothing to clear */
  }
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  token: string,
  path: string,
  init: RequestInit = {},
  fetchImpl: typeof fetch = fetch,
): Promise<T> {
  const res = await fetchImpl(path, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      Authorization: `Bearer ${token}`,
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
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export const listSessions = (token: string, limit = 100, offset = 0, f?: typeof fetch) =>
  request<SessionPage>(token, `/v1/sessions?limit=${limit}&offset=${offset}`, {}, f);

export const getSession = (token: string, id: string, f?: typeof fetch) =>
  request<SessionDetail>(token, `/v1/sessions/${encodeURIComponent(id)}`, {}, f);

export const viewerTicket = async (token: string, id: string, f?: typeof fetch) =>
  (
    await request<{ ticket: string; expires_in: number }>(
      token,
      `/v1/sessions/${encodeURIComponent(id)}/ticket`,
      { method: "POST", body: JSON.stringify({ role: "viewer" }) },
      f,
    )
  ).ticket;

/** Validate a token by making a cheap authenticated call. */
export async function checkToken(token: string, f?: typeof fetch): Promise<void> {
  await listSessions(token, 1, 0, f);
}
