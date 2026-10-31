import { describe, expect, it } from "vitest";
import {
  approveActions,
  actionEvents,
  editAction,
  rejectAction,
  retryAction,
  sessionActions,
} from "../src/api";
import {
  KIND_LABEL,
  describeAction,
  editFields,
  needsPolling,
  outcomeNote,
  pendingActions,
  resultLine,
  sortActions,
  eventText,
} from "../src/actions";
import type { ActionEvent, ActionItem } from "../src/types";

export const action = (over: Partial<ActionItem> = {}): ActionItem => ({
  id: "a1",
  session_id: "s1",
  ref: "p1",
  position: 1,
  kind: "task.create",
  state: "proposed",
  payload: { text: "Call the vendor", due: "2026-11-03" },
  payload_sha256: "f".repeat(64),
  evidence: [{ start_ms: 61_000, text: "add a task to call the vendor" }],
  proposed_by: "copilot:fast",
  approved_by: null,
  approved_at: null,
  result: null,
  executed_at: null,
  version: 1,
  created_at: "2026-10-30T12:00:00Z",
  updated_at: "2026-10-30T12:00:00Z",
  ...over,
});

describe("describing an action", () => {
  it("says what will be done for each kind", () => {
    expect(describeAction(action())).toEqual({ title: "Call the vendor", detail: "Due 2026-11-03" });
    expect(
      describeAction(
        action({ kind: "vault.append", payload: { path: "30 Resources/Sleep.md", text: "Magnesium helped." } }),
      ),
    ).toEqual({ title: "Magnesium helped.", detail: "Append to 30 Resources/Sleep.md" });
    expect(
      describeAction(
        action({
          kind: "reminder.schedule",
          payload: { title: "Send invoice", at: "2026-11-02T15:00:00+00:00", body: "Monday" },
        }),
        "UTC",
      ),
    ).toEqual({ title: "Send invoice", detail: "Nov 2, 2026, 3:00 PM · Monday" });
    expect(
      describeAction(
        action({ kind: "git.pr_comment", payload: { repo: "lordmuffin/jarvis-voice", pr: 214, body: "Needs a downgrade." } }),
      ),
    ).toEqual({ title: "Needs a downgrade.", detail: "Comment on lordmuffin/jarvis-voice#214" });
  });

  it("labels the kinds", () => {
    expect(KIND_LABEL["task.create"]).toBe("Task");
    expect(KIND_LABEL["git.pr_comment"]).toBe("PR comment");
  });

  it("offers only what the server lets you edit", () => {
    expect(editFields("task.create").map((f) => f.key)).toEqual(["text", "due"]);
    expect(editFields("vault.append").map((f) => f.key)).toEqual(["text"]);
    expect(editFields("reminder.schedule").map((f) => f.key)).toEqual(["title", "body"]);
    expect(editFields("git.pr_comment").map((f) => f.key)).toEqual(["body"]);
  });
});

describe("what happened", () => {
  it("links the pull request of a vault action, and says if it is still open", () => {
    const r = { landed: "merged", pr_url: "https://github.com/o/r/pull/9", merged: true };
    expect(resultLine(action({ state: "succeeded", result: r }))).toEqual({
      text: "Added to the vault",
      href: "https://github.com/o/r/pull/9",
      linkText: "pull request",
    });
    expect(resultLine(action({ state: "succeeded", result: { ...r, landed: "pr_open", merged: false } }))?.text).toBe(
      "Pull request open, not merged",
    );
  });

  it("reports a failure with its reason", () => {
    expect(resultLine(action({ state: "failed", result: { error: "GitHub: stale sha" } }))).toEqual({
      text: "Failed: GitHub: stale sha",
      tone: "error",
    });
  });

  it("follows a reminder to its delivery", () => {
    const base = { fire_at: "2026-11-02T15:00:00+00:00" };
    expect(resultLine(action({ kind: "reminder.schedule", state: "succeeded", result: { ...base, reminder_state: "pending" } }), "UTC")?.text).toBe(
      "Scheduled for Nov 2, 2026, 3:00 PM",
    );
    expect(resultLine(action({ kind: "reminder.schedule", state: "succeeded", result: { ...base, reminder_state: "sent" } }))?.text).toBe("Sent");
    expect(
      resultLine(action({ kind: "reminder.schedule", state: "succeeded", result: { ...base, reminder_state: "failed", reminder_error: "Gotify answered 502" } })),
    ).toEqual({ text: "Reminder not delivered: Gotify answered 502", tone: "error" });
  });

  it("links a posted comment", () => {
    const r = resultLine(
      action({ kind: "git.pr_comment", state: "succeeded", result: { landed: "posted", comment_url: "https://github.com/o/r/pull/1#c" } }),
    );
    expect(r).toEqual({ text: "Comment posted", href: "https://github.com/o/r/pull/1#c", linkText: "comment" });
  });

  it("has nothing to say about a proposal", () => {
    expect(resultLine(action())).toBeNull();
  });
});

describe("the list", () => {
  it("puts what needs you first, then runs, failures, results", () => {
    const items = [
      action({ id: "ok", state: "succeeded", position: 1 }),
      action({ id: "bad", state: "failed", position: 2 }),
      action({ id: "new2", state: "proposed", position: 4 }),
      action({ id: "run", state: "executing", position: 3 }),
      action({ id: "new1", state: "proposed", position: 3 }),
      action({ id: "no", state: "rejected", position: 5 }),
    ];
    expect(sortActions(items).map((i) => i.id)).toEqual(["new1", "new2", "run", "bad", "ok", "no"]);
    expect(pendingActions(items).map((i) => i.id)).toEqual(["new1", "new2"]);
  });

  it("keeps polling while something runs or a reminder has not been delivered", () => {
    expect(needsPolling([action()])).toBe(false);
    expect(needsPolling([action({ state: "approved" })])).toBe(true);
    expect(needsPolling([action({ state: "executing" })])).toBe(true);
    expect(
      needsPolling([action({ kind: "reminder.schedule", state: "succeeded", result: { reminder_state: "pending" } })]),
    ).toBe(true);
  });

  it("explains why an approval did not go through", () => {
    expect(outcomeNote("approved")).toBeNull();
    expect(outcomeNote("already")).toBeNull();
    expect(outcomeNote("stale")).toContain("changed");
    expect(outcomeNote("not_open")).toContain("rejected or expired");
  });
});

describe("the audit trail", () => {
  const ev = (event: string, actor_kind: ActionEvent["actor_kind"], payload = {}): ActionEvent => ({
    at: "2026-10-30T12:00:00Z",
    actor_user_id: null,
    actor_kind,
    event,
    payload,
  });
  it("reads as sentences", () => {
    expect(eventText(ev("proposed", "system", { proposed_by: "copilot:fast" }))).toBe("Proposed by copilot:fast");
    expect(eventText(ev("approved", "device"))).toBe("Approved from a device");
    expect(eventText(ev("approved", "web"))).toBe("Approved on the web");
    expect(eventText(ev("exec_failed", "system", { error: "boom" }))).toBe("Failed: boom");
    expect(eventText(ev("retry_requested", "web"))).toBe("Retry requested on the web");
    expect(eventText(ev("exec_succeeded", "system"))).toBe("Done");
  });
});

describe("api", () => {
  const calls: { url: string; init: RequestInit }[] = [];
  const f = (async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ enabled: true, items: [], results: [], events: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  it("talks to the review endpoints", async () => {
    await sessionActions("s 1", f);
    await approveActions("s1", [{ id: "a1", payload_sha256: "x" }], f);
    await editAction("a1", { text: "t" }, f);
    await rejectAction("a1", f);
    await retryAction("a1", f);
    await actionEvents("a1", f);
    expect(calls.map((c) => `${c.init.method ?? "GET"} ${c.url}`)).toEqual([
      "GET /v1/sessions/s%201/actions",
      "POST /v1/sessions/s1/actions/approve",
      "PATCH /v1/actions/a1",
      "POST /v1/actions/a1/reject",
      "POST /v1/actions/a1/retry",
      "GET /v1/actions/a1/events",
    ]);
    expect(JSON.parse(String(calls[1]!.init.body))).toEqual({ items: [{ id: "a1", payload_sha256: "x" }] });
    expect(JSON.parse(String(calls[2]!.init.body))).toEqual({ changes: { text: "t" } });
  });
});
