import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  audioUrl,
  createDevice,
  listSessions,
  loginUrl,
  logout,
  me,
  onUnauthorized,
  revokeDevice,
  sessionsPath,
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
});
