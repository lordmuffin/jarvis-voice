// @vitest-environment happy-dom
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { me } from "../src/store";
import type { WorkItem } from "../src/types";
import { WorkItemsPage } from "../src/views/WorkItemsPage";

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
  sources: [],
  locked_fields: [],
  merged_into: null,
  branch: "b",
  prompt_sha256: null,
  approved_at: null,
  version: 1,
  created_at: "2026-10-12T10:00:00Z",
  updated_at: "2026-10-12T10:00:00Z",
  problems: [],
  session_title: "ARC runner migration vendor call",
  session_started_at: "2026-10-14T15:05:00Z",
  ...over,
});

const ITEMS: WorkItem[] = [
  item({ id: "a", title: "Add ARM runner scale set", sources: [{ segment_id: "g1", start_ms: 872000, end_ms: 880000, text: "ARM?" }] }),
  item({ id: "b", title: "Scope scale sets to the org", state: "merged", merged_into: "a" }),
  item({ id: "c", title: "Node inventory export", state: "approved", session_id: "s2", session_title: "Platform sync", session_started_at: "2026-10-13T10:00:00Z" }),
  item({ id: "d", title: "Drop the old runner", state: "discarded", session_id: "s2", session_title: "Platform sync", session_started_at: "2026-10-13T10:00:00Z" }),
  item({ id: "e", title: "Orphan merge", state: "merged", merged_into: "gone", session_id: "s2", session_title: "Platform sync", session_started_at: "2026-10-13T10:00:00Z" }),
];

let root: HTMLElement;
let urls: string[];
const settle = async () => {
  for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
};

beforeEach(async () => {
  urls = [];
  root = document.createElement("div");
  document.body.appendChild(root);
  me.value = { id: "u1", work_items: true } as never;
  globalThis.fetch = vi.fn(async (url: string | URL | Request) => {
    urls.push(String(url));
    return new Response(JSON.stringify({ enabled: true, dispatch_enabled: false, repos: [], items: ITEMS }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  render(h(WorkItemsPage, {}), root);
  await settle();
});

afterEach(() => {
  render(null, root);
  root.remove();
  me.value = "loading";
  vi.restoreAllMocks();
});

const tab = (name: RegExp) => [...root.querySelectorAll<HTMLElement>("[role=tab]")].find((t) => name.test(t.textContent ?? ""))!;
const titles = () => [...root.querySelectorAll('[data-testid="work-item-title"]')].map((e) => e.textContent);

describe("work items page", () => {
  it("fetches every state once and counts them in the tabs", () => {
    expect(urls).toHaveLength(1);
    for (const s of ["draft", "approved", "discarded", "merged"]) expect(urls[0]).toContain(`state=${s}`);
    expect(["Open", "Discarded", "Merged"].map((n) => tab(new RegExp(n)).textContent)).toEqual(["Open 2", "Discarded 1", "Merged 2"]);
  });

  it("shows the open items first, grouped by session with its title and date", () => {
    expect(titles()).toEqual(["Add ARM runner scale set", "Node inventory export"]);
    const heads = [...root.querySelectorAll(".j-sec")].map((e) => e.textContent ?? "");
    expect(heads[0]).toContain("ARC runner migration vendor call");
    expect(heads[1]).toContain("Platform sync");
  });

  it("links a source to that moment in its own session, even under a session heading", () => {
    const a = root.querySelector<HTMLAnchorElement>(".i-src")!;
    expect(a.getAttribute("href")).toBe("/sessions/s1#t=872000");
    expect(a.textContent).toBe("▶ 14:32");
  });

  it("switches views without another request", async () => {
    tab(/Discarded/).click();
    await settle();
    expect(titles()).toEqual(["Drop the old runner"]);
    expect(urls).toHaveLength(1);
  });

  it("says what a merged item was merged into, and offers no actions on it", async () => {
    tab(/Merged/).click();
    await settle();
    expect(titles()).toEqual(["Scope scale sets to the org", "Orphan merge"]);
    const cards = [...root.querySelectorAll('[data-testid="work-item"]')];
    expect(cards[0]!.textContent).toContain("Merged into “Add ARM runner scale set”");
    expect(cards[1]!.textContent).toContain("Merged into another work item");
    expect(root.querySelectorAll('[data-state="merged"] button')).toHaveLength(0);
  });

  it("keeps the empty messages", async () => {
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ enabled: true, dispatch_enabled: false, repos: [], items: [] }), { status: 200, headers: { "Content-Type": "application/json" } })) as typeof fetch;
    render(null, root);
    render(h(WorkItemsPage, {}), root);
    await settle();
    expect(root.textContent).toContain("Nothing to review.");
    tab(/Discarded/).click();
    await settle();
    expect(root.textContent).toContain("Nothing discarded.");
    tab(/Merged/).click();
    await settle();
    expect(root.textContent).toContain("Nothing merged yet.");
  });
});
