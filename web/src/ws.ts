import { ApiError, getSession, viewerTicket } from "./api";
import type { Event } from "./reducer";
import type { ServerEvent, SessionDetail } from "./types";

export interface WatchOptions {
  sessionId: string;
  token: string;
  dispatch: (event: Event) => void;
  /** Called once the session has reached a terminal state (done/failed). */
  onFinished?: (detail: SessionDetail) => void;
  onUnauthorized?: () => void;
}

const BACKOFF_MS = [500, 1000, 2000, 4000];
// The server keeps a viewer socket open after the session ends, so poll the status too.
const STATUS_POLL_MS = 3000;

function streamUrl(sessionId: string, ticket: string): string {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}/v1/sessions/${encodeURIComponent(sessionId)}/stream?ticket=${encodeURIComponent(ticket)}`;
}

function openSocket(url: string, onMessage: (e: ServerEvent) => void): Promise<WebSocket> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    ws.onopen = () => resolve(ws);
    ws.onerror = () => reject(new Error("websocket failed"));
    ws.onmessage = (m) => {
      if (typeof m.data !== "string") return;
      try {
        onMessage(JSON.parse(m.data) as ServerEvent);
      } catch {
        /* ignore malformed frames */
      }
    };
  });
}

/**
 * Watch one session as a viewer: ticket -> socket -> snapshot, reconnecting with a fresh
 * ticket (they are single use) until the session is finished. Returns a stop function.
 *
 * The snapshot is fetched *after* the socket opens so nothing published in between is lost;
 * the reducer is idempotent, so overlap between snapshot and stream is harmless.
 */
export function watchSession(opts: WatchOptions): () => void {
  let stopped = false;
  let finished = false;
  let socket: WebSocket | null = null;
  let wake: (() => void) | null = null;

  const sleep = (ms: number) =>
    new Promise<void>((resolve) => {
      const t = setTimeout(resolve, ms);
      wake = () => {
        clearTimeout(t);
        resolve();
      };
    });

  async function snapshot(): Promise<SessionDetail> {
    const detail = await getSession(opts.token, opts.sessionId);
    opts.dispatch({ type: "snapshot", detail });
    return detail;
  }

  async function run(): Promise<void> {
    let attempt = 0;
    while (!stopped) {
      opts.dispatch({ type: "connection", state: "connecting" });
      try {
        const ticket = await viewerTicket(opts.token, opts.sessionId);
        const ws = await openSocket(streamUrl(opts.sessionId, ticket), (e) => opts.dispatch(e));
        socket = ws;
        const closed = new Promise<void>((resolve) => {
          ws.onclose = () => resolve();
          if (ws.readyState === WebSocket.CLOSED) resolve();
        });
        if (stopped) ws.close();
        attempt = 0;
        opts.dispatch({ type: "connection", state: "open" });
        const poll = setInterval(() => {
          snapshot().then(
            (d) => {
              if (d.status !== "done" && d.status !== "failed") return;
              finished = true;
              clearInterval(poll);
              ws.close();
              opts.onFinished?.(d);
            },
            () => undefined,
          );
        }, STATUS_POLL_MS);
        try {
          const first = await snapshot();
          if (first.status === "done" || first.status === "failed") {
            finished = true;
            ws.close();
            opts.onFinished?.(first);
          }
          await closed;
        } finally {
          clearInterval(poll);
        }
        if (finished) return;
      } catch (e) {
        socket?.close();
        if (e instanceof ApiError && e.status === 401) {
          opts.onUnauthorized?.();
          return;
        }
        if (e instanceof ApiError && e.status === 404) {
          opts.dispatch({ type: "error", code: "not_found", message: "Session not found." });
          return;
        }
      }
      socket = null;
      if (stopped) return;
      opts.dispatch({ type: "connection", state: "closed" });
      try {
        const detail = await snapshot();
        if (detail.status === "done" || detail.status === "failed") {
          opts.onFinished?.(detail);
          return;
        }
      } catch {
        /* retry below */
      }
      await sleep(BACKOFF_MS[Math.min(attempt++, BACKOFF_MS.length - 1)]!);
    }
  }

  void run();
  return () => {
    stopped = true;
    wake?.();
    socket?.close();
  };
}
