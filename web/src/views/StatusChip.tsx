import type { SessionStatus } from "../types";

const LABEL: Record<SessionStatus, string> = {
  live: "Live",
  finalizing: "Finalizing",
  done: "Done",
  failed: "Failed",
};

export function StatusChip({ status }: { status: SessionStatus }) {
  return (
    <span class={`chip chip-${status}`} data-testid="status-chip">
      {status === "live" && <span class="dot" aria-hidden="true" />}
      {status === "failed" && <span aria-hidden="true">✕ </span>}
      {status === "done" && <span aria-hidden="true">✓ </span>}
      {LABEL[status]}
    </span>
  );
}
