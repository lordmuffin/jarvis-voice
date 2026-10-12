import type { DispatchState, WorkItem, WorkItemState } from "./types";

export const STATE_LABEL: Record<WorkItemState, string> = {
  draft: "Draft",
  approved: "Approved",
  discarded: "Discarded",
  merged: "Merged",
};

/** Open items first (approved, then drafts), each in the order they were found. */
export function sortItems(items: WorkItem[]): WorkItem[] {
  const rank: Record<WorkItemState, number> = { approved: 0, draft: 1, discarded: 2, merged: 3 };
  return [...items].sort(
    (a, b) => rank[a.state] - rank[b.state] || a.created_at.localeCompare(b.created_at),
  );
}

/** Criteria as edited in a textarea: one per line, list markers and blanks dropped. */
export function parseCriteria(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.replace(/^\s*(?:[-*]\s*)?(?:\[[ xX]?\]\s*)?/, "").trim())
    .filter(Boolean);
}

/** `#t=<ms>` links into a session's transcript. */
export const transcriptHref = (sessionId: string, ms: number) =>
  `/sessions/${encodeURIComponent(sessionId)}#t=${ms}`;

/** The `t` of a `#t=<ms>` hash, or null. */
export function hashTime(hash: string): number | null {
  const m = /^#t=(\d+)$/.exec(hash);
  return m ? Number(m[1]) : null;
}

/**
 * Copy text to the clipboard. The async Clipboard API needs a secure context; the fallback
 * selects a hidden textarea and uses the legacy copy command.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
  }
}

export const DISPATCH_LABEL: Record<DispatchState, string> = {
  queued: "Queued",
  running: "Running",
  pr_opened: "Draft PR opened",
  no_changes: "No changes",
  failed: "Failed",
  rate_limited: "Hit the Claude usage limit",
  timed_out: "Timed out",
  cancelled: "Cancelled",
  needs_input: "Needs your input",
};

/** A run that is still going: poll, offer cancel, no second run. */
export const runActive = (item: WorkItem) =>
  item.dispatch?.state === "queued" || item.dispatch?.state === "running";
