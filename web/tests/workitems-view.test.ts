// @vitest-environment happy-dom
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkItem, WorkItemPage } from "../src/types";
import { me } from "../src/store";
import { POLL, SessionWorkItems, WorkItemList } from "../src/views/WorkItems";
import { copyText } from "../src/workitems";

const item = (over: Partial<WorkItem>): WorkItem => ({
  id: "i1",
  session_id: "s1",
  ref: "w1",
  state: "draft",
  title: "Retry Gotify",
  repo: "lordmuffin/jarvis-voice",
  intent: "Retry with backoff.",
  acceptance_criteria: ["Retried"],
  done_when: null,
  sources: [{ segment_id: "g1", start_ms: 21000, end_ms: 30000, text: "retry it" }],
  locked_fields: [],
  merged_into: null,
  branch: "claude/work-s1-w1-retry-gotify",
  prompt_sha256: null,
  approved_at: null,
  version: 1,
  created_at: "2026-10-12T10:00:00Z",
  updated_at: "2026-10-12T10:00:00Z",
  problems: [],
  ...over,
});

const page = (items: WorkItem[], dispatch = false): WorkItemPage => ({
  enabled: true,
  dispatch_enabled: dispatch,
  repos: [
    { name: "lordmuffin/jarvis-voice", description: "app" },
    { name: "lordmuffin/np-home-homelab", description: "homelab" },
  ],
  items,
});

type Call = { url: string; method: string; body: unknown };
let calls: Call[];
let root: HTMLElement;

/** A fetch answering every call with `body` (or 204 when undefined), recording it. */
function serve(body?: unknown) {
  globalThis.fetch = vi.fn(async (url: string | URL | Request, init: RequestInit = {}) => {
    calls.push({
      url: String(url),
      method: init.method ?? "GET",
      body: init.body ? JSON.parse(String(init.body)) : undefined,
    });
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status: body === undefined ? 204 : 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
}

/** happy-dom has no isSecureContext/confirm to spy on: define them for one test. */
function define(target: object, key: string, value: unknown) {
  Object.defineProperty(target, key, { value, configurable: true, writable: true });
}

const settle = async () => {
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
};

function show(items: WorkItem[], onChanged = vi.fn(), showSession = false, dispatch = false) {
  render(h(WorkItemList, { page: page(items, dispatch), onChanged, showSession }), root);
  return onChanged;
}

const run = (over: Partial<NonNullable<WorkItem["dispatch"]>>): NonNullable<WorkItem["dispatch"]> => ({
  id: "r1",
  attempt: 1,
  state: "running",
  repo: "lordmuffin/jarvis-voice",
  branch: "claude/work-s1-w1-retry-gotify",
  pr_url: null,
  pr_number: null,
  summary: null,
  error: null,
  questions: null,
  guidance: null,
  created_at: "2026-10-12T10:00:00Z",
  started_at: "2026-10-12T10:00:05Z",
  finished_at: null,
  ...over,
});

const button = (name: string) => {
  const b = [...root.querySelectorAll("button")].find((x) => x.textContent?.trim() === name);
  if (!b) throw new Error(`no button ${name}: ${root.innerHTML}`);
  return b as HTMLButtonElement;
};

beforeEach(() => {
  calls = [];
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  root.remove();
  vi.restoreAllMocks();
});

describe("work item cards", () => {
  it("lists approved items first and says what blocks a draft", () => {
    show([
      item({ id: "d", title: "Draft one", repo: null, problems: ["choose a repository"] }),
      item({ id: "a", title: "Approved one", state: "approved" }),
    ]);
    const titles = [...root.querySelectorAll('[data-testid="work-item-title"]')].map((e) => e.textContent);
    expect(titles).toEqual(["Approved one", "Draft one"]);
    expect(root.querySelector('[data-testid="work-item-problems"]')?.textContent).toContain(
      "choose a repository",
    );
    expect(button("✓ Approve").disabled).toBe(true);
    expect(root.textContent).toContain("no repository yet");
    // On a session's own page, a source is a hash link into its transcript.
    expect(root.querySelector(".i-src")?.getAttribute("href")).toBe("#t=21000");
  });

  it("approves a draft", async () => {
    serve(item({ state: "approved" }));
    const changed = show([item({})]);
    button("✓ Approve").click();
    await settle();
    expect(calls).toEqual([{ url: "/v1/work-items/i1/approve", method: "POST", body: undefined }]);
    expect(changed).toHaveBeenCalledOnce();
  });

  it("copies an approved item's frozen prompt and records the copy", async () => {
    const writeText = vi.fn(async () => undefined);
    define(navigator, "clipboard", { writeText });
    define(window, "isSecureContext", true);
    serve({ markdown: "# Task", frozen: true, sha256: "abc" });
    show([item({ state: "approved" })]);
    button("⎘ Copy Prompt").click();
    await settle();
    expect(writeText).toHaveBeenCalledWith("# Task");
    expect(calls.map((c) => [c.method, c.url])).toEqual([
      ["GET", "/v1/work-items/i1/prompt"],
      ["POST", "/v1/work-items/i1/copied"],
    ]);
    expect(root.textContent).toContain("✓ Copied");
  });

  it("previews the prompt of a draft", async () => {
    serve({ markdown: "# Preview", frozen: false, sha256: null });
    show([item({})]);
    button("Preview Prompt").click();
    await settle();
    expect(root.querySelector('[data-testid="work-item-prompt"]')?.textContent).toBe("# Preview");
    expect(root.textContent).toContain("Approving freezes the prompt");
    button("Hide Prompt").click();
    await settle();
    expect(root.querySelector('[data-testid="work-item-prompt"]')).toBeNull();
  });

  it("sends only the fields that were edited", async () => {
    serve(item({}));
    const changed = show([item({})]);
    button("Edit").click();
    await settle();
    const title = root.querySelector<HTMLInputElement>('input[id$="-title"]')!;
    title.value = "Retry Gotify pushes";
    title.dispatchEvent(new Event("input", { bubbles: true }));
    const criteria = root.querySelector<HTMLTextAreaElement>('textarea[id$="-criteria"]')!;
    criteria.value = "- Retried\n- Logged when it gives up";
    criteria.dispatchEvent(new Event("input", { bubbles: true }));
    const repo = root.querySelector<HTMLSelectElement>('select[id$="-repo"]')!;
    repo.value = "lordmuffin/np-home-homelab";
    repo.dispatchEvent(new Event("change", { bubbles: true }));
    await settle();
    button("Save").click();
    await settle();
    expect(calls).toEqual([
      {
        url: "/v1/work-items/i1",
        method: "PATCH",
        body: {
          title: "Retry Gotify pushes",
          repo: "lordmuffin/np-home-homelab",
          acceptance_criteria: ["Retried", "Logged when it gives up"],
        },
      },
    ]);
    expect(changed).toHaveBeenCalledOnce();
  });

  it("saving an unchanged form sends nothing", async () => {
    serve(item({}));
    show([item({})]);
    button("Edit").click();
    await settle();
    button("Save").click();
    await settle();
    expect(calls).toEqual([]);
    expect(root.querySelector("form")).toBeNull();
  });

  it("merges selected drafts of one session into the chosen one", async () => {
    serve(item({}));
    const changed = show([item({ id: "a", title: "First" }), item({ id: "b", title: "Second" })]);
    const boxes = root.querySelectorAll<HTMLInputElement>('[data-testid="work-item-pick"]');
    for (const b of boxes) {
      b.checked = true;
      b.dispatchEvent(new Event("change", { bubbles: true }));
      await settle();
    }
    const bar = root.querySelector('[data-testid="merge-bar"]')!;
    expect(bar.textContent).toContain("Merge 2 drafts into");
    [...bar.querySelectorAll("button")].find((b) => b.textContent === "Second")!.click();
    await settle();
    expect(calls).toEqual([
      { url: "/v1/work-items/merge", method: "POST", body: { into: "b", sources: ["a"] } },
    ]);
    expect(changed).toHaveBeenCalledOnce();
  });

  it("refuses to merge drafts from different sessions", async () => {
    show([item({ id: "a" }), item({ id: "b", session_id: "s2" })]);
    for (const b of root.querySelectorAll<HTMLInputElement>('[data-testid="work-item-pick"]')) {
      b.checked = true;
      b.dispatchEvent(new Event("change", { bubbles: true }));
      await settle();
    }
    expect(root.textContent).toContain("Only drafts from the same session can be merged.");
  });

  it("discards after confirming, and shows a server refusal", async () => {
    define(window, "confirm", () => true);
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "a merged work item cannot be discarded" }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
    ) as typeof fetch;
    const changed = show([item({})]);
    button("✕ Discard").click();
    await settle();
    expect(root.querySelector('[role="alert"]')?.textContent).toBe(
      "a merged work item cannot be discarded",
    );
    expect(changed).not.toHaveBeenCalled();
  });

  it("restores a discarded item and links sources across sessions", async () => {
    serve(item({}));
    const changed = show([item({ state: "discarded", session_title: "Friday" })], vi.fn(), true);
    expect(root.querySelector(".i-src")?.getAttribute("href")).toBe("/sessions/s1#t=21000");
    expect(root.textContent).toContain("Friday");
    button("↩ Restore").click();
    await settle();
    expect(calls[0]).toMatchObject({ url: "/v1/work-items/i1/restore", method: "POST" });
    expect(changed).toHaveBeenCalledOnce();
  });
});

describe("dispatch", () => {
  it("runs an approved item in Claude Code after confirming", async () => {
    define(window, "confirm", () => true);
    serve(item({ state: "approved", dispatch: run({ state: "queued" }) }));
    const changed = show([item({ state: "approved" })], vi.fn(), false, true);
    button("▶ Run in Claude Code").click();
    await settle();
    expect(calls).toEqual([{ url: "/v1/work-items/i1/dispatch", method: "POST", body: undefined }]);
    expect(changed).toHaveBeenCalledOnce();
  });

  it("offers no run when dispatch is off or the item is a draft", () => {
    show([item({ state: "approved" }), item({ id: "d" })]);
    expect(root.textContent).not.toContain("Run in Claude Code");
    render(null, root);
    show([item({ id: "d" })], vi.fn(), false, true);
    expect(root.textContent).not.toContain("Run in Claude Code");
  });

  it("shows a running run and cancels it", async () => {
    serve(item({ state: "approved", dispatch: run({ state: "cancelled" }) }));
    const changed = show([item({ state: "approved", dispatch: run({}) })], vi.fn(), false, true);
    expect(root.querySelector('[data-testid="dispatch"]')?.textContent).toContain("Running");
    expect(root.textContent).not.toContain("Run in Claude Code");
    button("■ Cancel Run").click();
    await settle();
    expect(calls).toEqual([
      { url: "/v1/work-items/i1/dispatch/cancel", method: "POST", body: undefined },
    ]);
    expect(changed).toHaveBeenCalledOnce();
  });

  it("links the draft PR a run opened", () => {
    const done = run({
      state: "pr_opened",
      pr_url: "https://github.com/lordmuffin/jarvis-voice/pull/140",
      pr_number: 140,
      summary: "Added retries.",
    });
    show([item({ state: "approved", dispatch: done })], vi.fn(), false, true);
    const link = root.querySelector<HTMLAnchorElement>('[data-testid="dispatch"] a')!;
    expect(link.textContent).toBe("Draft PR #140");
    expect(link.getAttribute("href")).toBe("https://github.com/lordmuffin/jarvis-voice/pull/140");
    expect(root.textContent).toContain("Added retries.");
    expect(root.textContent).toContain("▶ Run Again");
  });

  it("says why a run failed", () => {
    show(
      [item({ state: "approved", dispatch: run({ state: "failed", error: "tests failed" }) })],
      vi.fn(),
      false,
      true,
    );
    const d = root.querySelector('[data-testid="dispatch"]')!;
    expect(d.textContent).toContain("Failed");
    expect(d.textContent).toContain("tests failed");
  });
});

describe("steering a run", () => {
  const type = (el: HTMLTextAreaElement, text: string) => {
    el.value = text;
    el.dispatchEvent(new Event("input", { bubbles: true }));
  };
  const steer = () => root.querySelector<HTMLTextAreaElement>('[data-testid="steer"] textarea');

  it("shows a run's questions with the reply box open, and continues with the answer", async () => {
    serve(item({ state: "approved", dispatch: run({ state: "queued", attempt: 2 }) }));
    const asked = run({
      state: "needs_input",
      pr_url: "https://github.com/lordmuffin/jarvis-voice/pull/1",
      pr_number: 1,
      summary: "Created hello.md.",
      questions: "1. Keep the trailing newline?",
    });
    const changed = show([item({ state: "approved", dispatch: asked })], vi.fn(), false, true);
    const d = root.querySelector('[data-testid="dispatch"]')!;
    expect(d.textContent).toContain("Needs your input");
    expect(root.querySelector('[data-testid="dispatch-questions"]')?.textContent).toContain(
      "1. Keep the trailing newline?",
    );
    expect(button("↪ Continue").disabled).toBe(true); // nothing typed yet
    type(steer()!, "  Yes, keep it.  ");
    await settle();
    button("↪ Continue").click();
    await settle();
    expect(calls).toEqual([
      {
        url: "/v1/work-items/i1/dispatch/continue",
        method: "POST",
        body: { guidance: "Yes, keep it." },
      },
    ]);
    expect(changed).toHaveBeenCalledOnce();
  });

  it("lets the owner steer any finished run", async () => {
    show([item({ state: "approved", dispatch: run({ state: "failed", error: "tests failed" }) })], vi.fn(), false, true);
    expect(steer()).toBeNull();
    button("↪ Steer Next Run").click();
    await settle();
    expect(steer()).not.toBeNull();
    button("Cancel").click();
    await settle();
    expect(steer()).toBeNull();
  });

  it("opens the reply box again when a later run asks", async () => {
    const asks = (id: string) => run({ id, state: "needs_input", questions: "1. Which?" });
    show([item({ state: "approved", dispatch: asks("r1") })], vi.fn(), false, true);
    button("Cancel").click();
    await settle();
    expect(steer()).toBeNull();
    show([item({ state: "approved", dispatch: asks("r2") })], vi.fn(), false, true);
    await settle();
    expect(steer()).not.toBeNull();
  });

  it("offers no steering while a run is going, or when dispatch is off", () => {
    show([item({ state: "approved", dispatch: run({ state: "running" }) })], vi.fn(), false, true);
    expect(root.textContent).not.toContain("Steer Next Run");
    render(null, root);
    show([item({ state: "approved", dispatch: run({ state: "needs_input", questions: "?" }) })]);
    expect(steer()).toBeNull();
    expect(root.textContent).not.toContain("Steer Next Run");
  });

  it("shows the guidance a follow-up run was given", () => {
    const followUp = run({ state: "running", attempt: 2, guidance: "Cap it at 60 s." });
    show([item({ state: "approved", dispatch: followUp })], vi.fn(), false, true);
    const d = root.querySelector('[data-testid="dispatch"]')!;
    expect(d.textContent).toContain("attempt 2");
    expect(d.textContent).toContain("Cap it at 60 s.");
  });
});

describe("polling", () => {
  it("refreshes a past session's items while a run is going, and stops after", async () => {
    me.value = {
      id: "u1",
      email: null,
      display_name: "Owner",
      is_admin: true,
      device_id: null,
      can_publish: true,
      auto_publish: false,
      work_items: true,
    };
    POLL.ms = 20;
    const pages = [
      page([item({ state: "approved", dispatch: run({ state: "running" }) })], true),
      page(
        [item({ state: "approved", dispatch: run({ state: "pr_opened", pr_number: 3, pr_url: "https://x/pull/3" }) })],
        true,
      ),
    ];
    let n = 0;
    globalThis.fetch = vi.fn(async () => {
      const body = pages[Math.min(n++, pages.length - 1)];
      return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    render(h(SessionWorkItems, { sessionId: "s1", ownerId: "u1", live: false }), root);
    await settle();
    expect(root.textContent).toContain("Running");
    await new Promise((r) => setTimeout(r, 60));
    await settle();
    expect(root.textContent).toContain("Draft PR #3");
    const after = n;
    await new Promise((r) => setTimeout(r, 100));
    expect(n).toBe(after); // finished: no more polling
    POLL.ms = 5000;
  });
});

describe("copyText", () => {
  it("falls back to a selected textarea outside a secure context", async () => {
    define(window, "isSecureContext", false);
    const exec = vi.fn(() => true);
    define(document, "execCommand", exec);
    expect(await copyText("hello")).toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
    expect(document.querySelector("textarea")).toBeNull(); // cleaned up
  });
});
