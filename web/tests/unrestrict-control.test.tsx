// @vitest-environment happy-dom
import { render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UnrestrictControl } from "../src/jarvis/UnrestrictControl";
import { me } from "../src/store";
import type { SessionDetail } from "../src/types";

const detail = (over: Partial<SessionDetail> = {}): SessionDetail =>
  ({ id: "s1", restricted: true, owner: { id: "u1", display_name: "Andrew" }, ...over }) as SessionDetail;

let root: HTMLElement;
const button = () => [...root.querySelectorAll("button")].find((b) => b.textContent === "Remove restriction");

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
  me.value = { id: "u1" } as never;
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("UnrestrictControl", () => {
  it("is offered to the owner of a restricted session", () => {
    render(<UnrestrictControl detail={detail()} onChanged={() => undefined} />, root);
    expect(button()).toBeDefined();
  });

  it("is not shown for an ordinary session or someone else's", () => {
    render(<UnrestrictControl detail={detail({ restricted: false })} onChanged={() => undefined} />, root);
    expect(button()).toBeUndefined();
    render(
      <UnrestrictControl detail={detail({ owner: { id: "u2", display_name: "Sam" } })} onChanged={() => undefined} />,
      root,
    );
    expect(button()).toBeUndefined();
  });

  it("lifts the restriction, then hands the reloaded session on", async () => {
    const after = detail({ restricted: false });
    const replies = [json({ id: "s1", restricted: false }), json(after)];
    const calls: string[] = [];
    globalThis.fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push(`${init.method ?? "GET"} ${url}`);
      return replies.shift()!;
    }) as unknown as typeof fetch;
    const changed = vi.fn();
    render(<UnrestrictControl detail={detail()} onChanged={changed} />, root);

    button()!.click();
    await vi.waitFor(() => expect(changed).toHaveBeenCalledWith(after));
    expect(calls).toEqual(["POST /v1/sessions/s1/unrestrict", "GET /v1/sessions/s1?include_deleted=true"]);
  });

  it("says why when the server refuses", async () => {
    globalThis.fetch = vi.fn(async () =>
      json({ detail: "The note is being written. Try again when it is done." }, 409),
    ) as unknown as typeof fetch;
    render(<UnrestrictControl detail={detail()} onChanged={() => undefined} />, root);
    button()!.click();
    await vi.waitFor(() => expect(root.textContent).toContain("being written"));
  });
});
