import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  audioUrl,
  createDevice,
  editNote,
  getPublishing,
  health,
  listSessions,
  loginUrl,
  logout,
  me,
  onUnauthorized,
  publicationAction,
  publicationRequest,
  publishAll,
  regenerateNote,
  revokeDevice,
  sessionsPath,
  setAutoPublish,
} from "../src/api";

type Call = { url: string; init: RequestInit };

/** A fetch that records its calls and answers with `status` and `body`. */
function fakeFetch(status: number, body?: unknown) {
  const calls: Call[] = [];
  const f = (async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init });
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  return { f, calls };
}

afterEach(() => onUnauthorized(() => undefined));

describe("api", () => {
  it("sends the cookie, never a bearer token", async () => {
    const { f, calls } = fakeFetch(200, { id: "u1", display_name: "Alice" });
    expect(await me(f)).toMatchObject({ display_name: "Alice" });
    expect(calls[0]!.url).toBe("/v1/me");
    expect(calls[0]!.init.credentials).toBe("same-origin");
    expect(JSON.stringify(calls[0]!.init.headers)).not.toContain("Authorization");
  });

  it("builds the session list query, admin filters included", () => {
    expect(sessionsPath()).toBe("/v1/sessions?limit=100&offset=0");
    expect(sessionsPath({ limit: 5, offset: 10, all: true })).toBe(
      "/v1/sessions?limit=5&offset=10&all=true",
    );
    expect(sessionsPath({ userId: "u 1" })).toBe("/v1/sessions?limit=100&offset=0&user_id=u+1");
  });

  it("lists sessions with the filter", async () => {
    const { f, calls } = fakeFetch(200, { items: [], total: 0, limit: 20, offset: 0 });
    await listSessions({ limit: 20, all: true }, f);
    expect(calls[0]!.url).toBe("/v1/sessions?limit=20&offset=0&all=true");
  });

  it("posts JSON for writes and accepts 204 with no body", async () => {
    const created = fakeFetch(201, { id: "d1", name: "Mac", token: "secret" });
    expect((await createDevice("Mac", created.f)).token).toBe("secret");
    expect(created.calls[0]!.init).toMatchObject({ method: "POST", body: '{"name":"Mac"}' });

    const gone = fakeFetch(204);
    await expect(revokeDevice("d/1", gone.f)).resolves.toBeUndefined();
    expect(gone.calls[0]!.url).toBe("/v1/devices/d%2F1");
    expect(gone.calls[0]!.init.method).toBe("DELETE");

    const out = fakeFetch(204);
    await logout(out.f);
    expect(out.calls[0]).toMatchObject({ url: "/auth/logout", init: { method: "POST" } });
  });

  it("reports a 401 to the sign-out handler and throws ApiError", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    const { f } = fakeFetch(401, { detail: "not signed in" });
    const err = await me(f).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(401);
    expect((err as ApiError).message).toBe("not signed in");
    expect(handler).toHaveBeenCalledOnce();
  });

  it("does not sign out on other errors", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    const { f } = fakeFetch(403, { detail: "admin only" });
    await expect(listSessions({ all: true }, f)).rejects.toMatchObject({ status: 403 });
    expect(handler).not.toHaveBeenCalled();
  });

  it("builds sign-in and audio URLs", () => {
    expect(loginUrl("/sessions/a?b=1")).toBe(
      "/auth/login?client=web&return_to=%2Fsessions%2Fa%3Fb%3D1",
    );
    expect(audioUrl("s1", "system")).toBe("/v1/sessions/s1/audio/system.wav");
  });

  it("routes each publishing action to its endpoint", () => {
    expect(publicationRequest("s 1", "publish")).toEqual({
      path: "/v1/sessions/s%201/publication",
      method: "POST",
    });
    expect(publicationRequest("s1", "merge")).toEqual({
      path: "/v1/sessions/s1/publication/merge",
      method: "POST",
    });
    expect(publicationRequest("s1", "close").path).toBe("/v1/sessions/s1/publication/close");
    expect(publicationRequest("s1", "skip")).toEqual({
      path: "/v1/sessions/s1/publication/skip",
      method: "POST",
    });
    expect(publicationRequest("s1", "unskip")).toEqual({
      path: "/v1/sessions/s1/publication/skip",
      method: "DELETE",
    });
  });

  it("edits, regenerates and publishes notes", async () => {
    const edit = fakeFetch(200, { id: "s1" });
    await editNote("s1", "# Note", edit.f);
    expect(edit.calls[0]).toMatchObject({
      url: "/v1/sessions/s1/note",
      init: { method: "PUT", body: '{"markdown":"# Note"}' },
    });

    const regen = fakeFetch(200, { id: "s1", status: "finalizing" });
    expect((await regenerateNote("s1", regen.f)).status).toBe("finalizing");
    expect(regen.calls[0]).toMatchObject({ url: "/v1/sessions/s1/regenerate", init: { method: "POST" } });

    const merge = fakeFetch(409, { detail: "no open pull request" });
    await expect(publicationAction("s1", "merge", merge.f)).rejects.toMatchObject({
      status: 409,
      message: "no open pull request",
    });
  });

  it("reads the queue, publishes all, and toggles auto-publish", async () => {
    const q = fakeFetch(200, { repo: "o/r", ready: [], open: [], recent: [] });
    expect((await getPublishing(q.f)).repo).toBe("o/r");
    expect(q.calls[0]!.url).toBe("/v1/publishing");

    const all = fakeFetch(202, { status: "started" });
    expect((await publishAll(all.f)).status).toBe("started");
    expect(all.calls[0]).toMatchObject({ url: "/v1/publishing/publish-all", init: { method: "POST" } });

    const auto = fakeFetch(200, { auto_publish: true });
    expect((await setAutoPublish(true, auto.f)).auto_publish).toBe(true);
    expect(auto.calls[0]).toMatchObject({
      url: "/v1/me",
      init: { method: "PATCH", body: '{"auto_publish":true}' },
    });
  });

  it("reads the release channel from /healthz", async () => {
    const { f, calls } = fakeFetch(200, { status: "ok", environment: "lab", version: "0.11.0" });
    expect((await health(f)).environment).toBe("lab");
    expect(calls[0]).toMatchObject({ url: "/healthz" });
  });
});
