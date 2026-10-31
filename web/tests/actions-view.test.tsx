// @vitest-environment happy-dom
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { me } from "../src/store";
import type { ActionItem, ActionPage } from "../src/types";
import { ActionList, POLL, SessionActions } from "../src/views/SessionActions";
import { action } from "./actions.test";

type Call = { url: string; method: string; body: unknown };
let calls: Call[];
let root: HTMLElement;

/** A fetch answering with `answer(url, method)` (or `{}`), recording every call. */
function serve(answer: (url: string, method: string) => unknown = () => ({})) {
  globalThis.fetch = vi.fn(async (url: string | URL | Request, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    calls.push({ url: String(url), method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(answer(String(url), method)), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
}

const settle = async () => {
  for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
};

const page = (items: ActionItem[]): ActionPage => ({ enabled: true, items });

function show(items: ActionItem[], onChanged = vi.fn()) {
  render(h(ActionList, { page: page(items), onChanged }), root);
  return onChanged;
}

const button = (label: string | RegExp, within: ParentNode = root) =>
  [...within.querySelectorAll("button")].find((b) => (typeof label === "string" ? b.textContent === label : label.test(b.textContent ?? "")));
const click = async (b: HTMLElement | undefined) => {
  expect(b, "button").toBeTruthy();
  b!.click();
  await settle();
};
const cards = () => [...root.querySelectorAll<HTMLElement>('[data-testid="action"]')];

beforeEach(() => {
  calls = [];
  root = document.createElement("div");
  document.body.appendChild(root);
  serve();
});
afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  me.value = "loading";
  vi.restoreAllMocks();
});

describe("a proposed action", () => {
  it("shows what it will do, where it was said, and who proposed it", () => {
    show([action()]);
    const [c] = cards();
    expect(c!.textContent).toContain("Task");
    expect(c!.textContent).toContain("Call the vendor");
    expect(c!.textContent).toContain("Due 2026-11-03");
    expect(c!.textContent).toContain("Proposed");
    const src = c!.querySelector("a[href='#t=61000']");
    expect(src?.textContent).toContain("1:01");
    expect(src?.getAttribute("title")).toBe("add a task to call the vendor");
  });

  it("approves with one tap, binding the approval to what is on screen", async () => {
    const onChanged = show([action({ payload_sha256: "a".repeat(64) })]);
    serve(() => ({ results: [{ id: "a1", outcome: "approved" }] }));
    await click(button("✓ Approve"));
    expect(calls).toEqual([
      {
        url: "/v1/sessions/s1/actions/approve",
        method: "POST",
        body: { items: [{ id: "a1", payload_sha256: "a".repeat(64) }] },
      },
    ]);
    expect(onChanged).toHaveBeenCalled();
  });

  it("says so when the action changed under you, and reloads", async () => {
    const onChanged = show([action()]);
    serve(() => ({ results: [{ id: "a1", outcome: "stale" }] }));
    await click(button("✓ Approve"));
    expect(root.textContent).toContain("changed");
    expect(onChanged).toHaveBeenCalled();
  });

  it("rejects with one tap", async () => {
    const onChanged = show([action()]);
    await click(button("✕ Reject"));
    expect(calls).toEqual([{ url: "/v1/actions/a1/reject", method: "POST", body: undefined }]);
    expect(onChanged).toHaveBeenCalled();
  });

  it("edits only what changed", async () => {
    const onChanged = show([action()]);
    await click(button("Edit"));
    const text = root.querySelector<HTMLTextAreaElement | HTMLInputElement>("[id$='-text']")!;
    expect(text.value).toBe("Call the vendor");
    text.value = "Call the vendor Monday";
    text.dispatchEvent(new Event("input", { bubbles: true }));
    await settle();
    await click(button("Save"));
    expect(calls).toEqual([{ url: "/v1/actions/a1", method: "PATCH", body: { changes: { text: "Call the vendor Monday" } } }]);
    expect(onChanged).toHaveBeenCalled();
  });

  it("shows a server refusal instead of failing silently", async () => {
    show([action()]);
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ detail: "that is not a valid action" }), { status: 422, headers: { "Content-Type": "application/json" } })) as typeof fetch;
    await click(button("Edit"));
    await click(button("Save")); // nothing changed: just closes
    await click(button("Edit"));
    const text = root.querySelector<HTMLInputElement>("[id$='-text']")!;
    text.value = "x";
    text.dispatchEvent(new Event("input", { bubbles: true }));
    await settle();
    await click(button("Save"));
    expect(root.textContent).toContain("that is not a valid action");
  });
});

describe("after approval", () => {
  it("shows a run, a result with its link, and a failure with Retry", async () => {
    const onChanged = show([
      action({ id: "r", state: "executing", position: 1 }),
      action({
        id: "ok",
        state: "succeeded",
        position: 2,
        result: { landed: "merged", pr_url: "https://github.com/o/r/pull/9", merged: true },
      }),
      action({ id: "bad", state: "failed", position: 3, result: { error: "GitHub: stale sha" } }),
    ]);
    const [running, failed, done] = cards();
    expect(running!.textContent).toContain("Running");
    expect(failed!.textContent).toContain("Failed: GitHub: stale sha");
    expect(done!.querySelector("a[href='https://github.com/o/r/pull/9']")).toBeTruthy();
    expect(button("✓ Approve", done)).toBeUndefined();
    await click(button("↻ Retry", failed));
    expect(calls).toEqual([{ url: "/v1/actions/bad/retry", method: "POST", body: undefined }]);
    expect(onChanged).toHaveBeenCalled();
  });

  it("offers the audit trail", async () => {
    show([action({ state: "succeeded", result: { landed: "merged" } })]);
    serve(() => ({
      events: [
        { at: "2026-10-30T12:00:00Z", actor_user_id: null, actor_kind: "system", event: "proposed", payload: { proposed_by: "copilot:fast" } },
        { at: "2026-10-30T12:01:00Z", actor_user_id: "u1", actor_kind: "device", event: "approved", payload: {} },
        { at: "2026-10-30T12:01:05Z", actor_user_id: null, actor_kind: "system", event: "exec_succeeded", payload: {} },
      ],
    }));
    await click(button("History"));
    const items = [...root.querySelectorAll('[data-testid="action-history"] li')].map((li) => li.textContent);
    expect(items).toEqual([
      expect.stringContaining("Proposed by copilot:fast"),
      expect.stringContaining("Approved from a device"),
      expect.stringContaining("Done"),
    ]);
    await click(button("Hide History"));
    expect(root.querySelector('[data-testid="action-history"]')).toBeNull();
  });
});

describe("approving the list", () => {
  it("approves every pending action in order, each bound to its own content", async () => {
    const onChanged = show([
      action({ id: "b", position: 2, payload_sha256: "b".repeat(64) }),
      action({ id: "a", position: 1, payload_sha256: "a".repeat(64) }),
      action({ id: "x", position: 3, state: "succeeded" }),
    ]);
    serve(() => ({ results: [] }));
    await click(button("✓ Approve all (2)"));
    expect(calls).toEqual([
      {
        url: "/v1/sessions/s1/actions/approve",
        method: "POST",
        body: {
          items: [
            { id: "a", payload_sha256: "a".repeat(64) },
            { id: "b", payload_sha256: "b".repeat(64) },
          ],
        },
      },
    ]);
    expect(onChanged).toHaveBeenCalled();
  });

  it("is offered only for two or more", () => {
    show([action()]);
    expect(button(/Approve all/)).toBeUndefined();
  });
});

describe("the session section", () => {
  const owner = {
    id: "u1",
    email: null,
    display_name: "Owner",
    is_admin: true,
    device_id: null,
    can_publish: true,
    auto_publish: false,
    work_items: false,
    actions: true,
  };

  it("shows nothing to anyone but the owner, or when the feature is off", async () => {
    serve(() => page([action()]));
    me.value = { ...owner, actions: false };
    render(h(SessionActions, { sessionId: "s1", ownerId: "u1", live: false }), root);
    await settle();
    expect(root.querySelector('[data-testid="actions"]')).toBeNull();
    expect(calls).toEqual([]);
    me.value = { ...owner, id: "someone-else" };
    render(h(SessionActions, { sessionId: "s1", ownerId: "u1", live: false }), root);
    await settle();
    expect(root.querySelector('[data-testid="actions"]')).toBeNull();
  });

  it("lists the owner's proposals, and polls while something is running", async () => {
    me.value = owner;
    POLL.ms = 20;
    const pages = [page([action({ state: "executing" })]), page([action({ state: "succeeded", result: { landed: "merged" } })])];
    let n = 0;
    serve(() => pages[Math.min(n++, pages.length - 1)]);
    render(h(SessionActions, { sessionId: "s1", ownerId: "u1", live: false }), root);
    await settle();
    expect(root.querySelector('[data-testid="actions"]')).toBeTruthy();
    await new Promise((r) => setTimeout(r, 120));
    expect(cards()[0]!.getAttribute("data-state")).toBe("succeeded");
    const fetched = calls.length;
    await new Promise((r) => setTimeout(r, 120));
    expect(calls.length).toBe(fetched); // settled: it stopped
  });

  it("stays out of a finished session with nothing proposed", async () => {
    me.value = owner;
    serve(() => page([]));
    render(h(SessionActions, { sessionId: "s1", ownerId: "u1", live: false }), root);
    await settle();
    expect(root.querySelector('[data-testid="actions"]')).toBeNull();
  });
});
