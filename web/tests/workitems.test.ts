import { describe, expect, it } from "vitest";
import {
  editWorkItem,
  mergeWorkItems,
  myWorkItems,
  sessionWorkItems,
  workItemAction,
  workItemCopied,
  workItemPrompt,
} from "../src/api";
import { parseRoute } from "../src/route";
import type { WorkItem } from "../src/types";
import { hashTime, parseCriteria, sortItems, transcriptHref } from "../src/workitems";

function fakeFetch(status: number, body?: unknown) {
  const calls: { url: string; init: RequestInit }[] = [];
  const f = (async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init });
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  return { f, calls };
}

const item = (over: Partial<WorkItem>): WorkItem => ({
  id: "i",
  session_id: "s",
  ref: "w1",
  state: "draft",
  title: "T",
  repo: null,
  intent: "",
  acceptance_criteria: [],
  done_when: null,
  sources: [],
  locked_fields: [],
  merged_into: null,
  branch: "claude/work-s-w1-t",
  prompt_sha256: null,
  approved_at: null,
  done_at: null,
  merged_pr_url: null,
  version: 1,
  created_at: "2026-10-12T10:00:00Z",
  updated_at: "2026-10-12T10:00:00Z",
  problems: [],
  ...over,
});

describe("work item api", () => {
  it("reads a session's items and the cross-session queue", async () => {
    const page = { enabled: true, dispatch_enabled: false, repos: [], items: [] };
    const a = fakeFetch(200, page);
    await sessionWorkItems("s/1", a.f);
    expect(a.calls[0]!.url).toBe("/v1/sessions/s%2F1/work-items");
    const b = fakeFetch(200, page);
    await myWorkItems([], b.f);
    await myWorkItems(["discarded"], b.f);
    expect(b.calls.map((c) => c.url)).toEqual(["/v1/work-items", "/v1/work-items?state=discarded"]);
  });

  it("edits, acts, merges, previews and records copies", async () => {
    const { f, calls } = fakeFetch(200, item({}));
    await editWorkItem("i", { title: "New", repo: null }, f);
    await workItemAction("i", "approve", f);
    await mergeWorkItems("i", ["j", "k"], f);
    expect(calls.map((c) => [c.url, c.init.method, c.init.body])).toEqual([
      ["/v1/work-items/i", "PATCH", '{"title":"New","repo":null}'],
      ["/v1/work-items/i/approve", "POST", undefined],
      ["/v1/work-items/merge", "POST", '{"into":"i","sources":["j","k"]}'],
    ]);
    const p = fakeFetch(200, { markdown: "# x", frozen: true, sha256: "ab" });
    expect((await workItemPrompt("i", p.f)).frozen).toBe(true);
    const c = fakeFetch(204);
    await workItemCopied("i", c.f);
    expect(c.calls[0]).toMatchObject({ url: "/v1/work-items/i/copied", init: { method: "POST" } });
  });
});

describe("work item helpers", () => {
  it("parses criteria from a textarea, list markers and blanks dropped", () => {
    expect(parseCriteria("- [ ] one\n\n* two\n[x] three\n  four  \n")).toEqual([
      "one",
      "two",
      "three",
      "four",
    ]);
  });

  it("sorts approved, then drafts, then closed, oldest first", () => {
    const out = sortItems([
      item({ id: "d2", created_at: "2026-10-12T10:05:00Z" }),
      item({ id: "x", state: "discarded" }),
      item({ id: "d", state: "done" }),
      item({ id: "a", state: "approved", created_at: "2026-10-12T11:00:00Z" }),
      item({ id: "d1" }),
    ]);
    expect(out.map((i) => i.id)).toEqual(["a", "d1", "d2", "x", "d"]);
  });

  it("links into the transcript and reads the link back", () => {
    expect(transcriptHref("a b", 21000)).toBe("/sessions/a%20b#t=21000");
    expect(hashTime("#t=21000")).toBe(21000);
    expect(hashTime("#t=")).toBeNull();
    expect(hashTime("")).toBeNull();
  });

  it("routes /work-items", () => {
    expect(parseRoute("/work-items")).toEqual({ name: "work-items" });
  });
});
