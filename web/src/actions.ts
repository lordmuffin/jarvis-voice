import type { ActionEvent, ActionItem, ActionKind, ApprovalOutcome } from "./types";

export const KIND_LABEL: Record<ActionKind, string> = {
  "task.create": "Task",
  "vault.append": "Note",
  "reminder.schedule": "Reminder",
  "git.pr_comment": "PR comment",
};

export const STATE_LABEL: Record<ActionItem["state"], string> = {
  proposed: "Proposed",
  approved: "Approved",
  executing: "Running",
  succeeded: "Done",
  failed: "Failed",
  rejected: "Rejected",
  expired: "Expired",
};

const str = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : String(v));

/** "Nov 2, 2026, 3:00 PM" in `timeZone` (the viewer's, by default). */
export function formatWhen(iso: string, timeZone?: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short", timeZone })
    .format(d)
    .replace(/ /g, " ");
}

/** What the action will do, in a title and one line of detail. */
export function describeAction(item: ActionItem, timeZone?: string): { title: string; detail?: string } {
  const p = item.payload;
  switch (item.kind) {
    case "task.create":
      return { title: str(p.text), ...(p.due ? { detail: `Due ${str(p.due)}` } : {}) };
    case "vault.append":
      return { title: str(p.text), detail: `Append to ${str(p.path)}` };
    case "reminder.schedule": {
      const when = formatWhen(str(p.at), timeZone);
      return { title: str(p.title), detail: p.body ? `${when} · ${str(p.body)}` : when };
    }
    case "git.pr_comment":
      return { title: str(p.body), detail: `Comment on ${str(p.repo)}#${str(p.pr)}` };
  }
}

/** The fields the server lets the owner change before approving. */
export function editFields(kind: ActionKind): { key: string; label: string; multiline: boolean }[] {
  switch (kind) {
    case "task.create":
      return [
        { key: "text", label: "Task", multiline: true },
        { key: "due", label: "Due (a date, or words)", multiline: false },
      ];
    case "vault.append":
      return [{ key: "text", label: "Text to append", multiline: true }];
    case "reminder.schedule":
      return [
        { key: "title", label: "Title", multiline: false },
        { key: "body", label: "Message", multiline: true },
      ];
    case "git.pr_comment":
      return [{ key: "body", label: "Comment", multiline: true }];
  }
}

export interface ResultLine {
  text: string;
  href?: string;
  linkText?: string;
  tone?: "error";
}

/** What became of an approved action, or null while there is nothing to report. */
export function resultLine(item: ActionItem, timeZone?: string): ResultLine | null {
  const r = item.result ?? {};
  if (item.state === "failed") {
    const why = str(r.error);
    return { text: why ? `Failed: ${why}` : "Failed", tone: "error" };
  }
  if (item.state !== "succeeded") return null;
  if (item.kind === "reminder.schedule") {
    switch (str(r.reminder_state)) {
      case "sent":
        return { text: "Sent" };
      case "sending":
        return { text: "Sending…" };
      case "failed":
        return { text: `Reminder not delivered: ${str(r.reminder_error)}`, tone: "error" };
      default:
        return { text: `Scheduled for ${formatWhen(str(r.fire_at), timeZone)}` };
    }
  }
  if (item.kind === "git.pr_comment") {
    return r.comment_url
      ? { text: "Comment posted", href: str(r.comment_url), linkText: "comment" }
      : { text: "Comment posted" };
  }
  const link = r.pr_url ? { href: str(r.pr_url), linkText: "pull request" } : {};
  if (str(r.landed) === "pr_open") return { text: "Pull request open, not merged", ...link };
  if (str(r.landed) === "already_present" && !r.pr_url) return { text: "Already in the vault" };
  return { text: "Added to the vault", ...link };
}

const RANK: Record<ActionItem["state"], number> = {
  proposed: 0,
  approved: 1,
  executing: 1,
  failed: 2,
  succeeded: 3,
  rejected: 4,
  expired: 5,
};

/** What needs you first, then what is running, then failures, then what is done. */
export function sortActions(items: ActionItem[]): ActionItem[] {
  return [...items].sort((a, b) => RANK[a.state] - RANK[b.state] || a.position - b.position);
}

export const pendingActions = (items: ActionItem[]): ActionItem[] =>
  sortActions(items).filter((i) => i.state === "proposed");

/** Something is still moving: an action running, or a reminder not yet delivered. */
export const needsPolling = (items: ActionItem[]): boolean =>
  items.some(
    (i) =>
      i.state === "approved" ||
      i.state === "executing" ||
      (i.kind === "reminder.schedule" &&
        i.state === "succeeded" &&
        ["pending", "sending"].includes(str(i.result?.reminder_state))),
  );

/** Why an approval did not go through, or null if it did. */
export function outcomeNote(outcome: ApprovalOutcome): string | null {
  switch (outcome) {
    case "stale":
      return "This action changed since you saw it. Review it and approve again.";
    case "not_open":
      return "This action was already rejected or expired.";
    case "not_found":
      return "This action no longer exists.";
    default:
      return null;
  }
}

const VIA = { web: "on the web", device: "from a device", system: "" } as const;

/** One audit-log entry as a sentence. */
export function eventText(e: ActionEvent): string {
  const via = VIA[e.actor_kind];
  const by = (verb: string) => (via ? `${verb} ${via}` : verb);
  switch (e.event) {
    case "proposed":
      return `Proposed by ${str(e.payload.proposed_by) || "the copilot"}`;
    case "edited":
      return by("Edited");
    case "approved":
      return by("Approved");
    case "rejected":
      return by("Rejected");
    case "retry_requested":
      return by("Retry requested");
    case "exec_started":
      return "Started";
    case "exec_succeeded":
      return "Done";
    case "exec_failed":
      return `Failed: ${str(e.payload.error)}`;
    case "expired":
      return "Expired unreviewed";
    case "reconciled":
      return "Found already done";
    case "reminder_sent":
      return "Reminder sent";
    case "reminder_failed":
      return `Reminder not delivered: ${str(e.payload.error)}`;
    default:
      return e.event;
  }
}
