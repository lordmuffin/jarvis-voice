import type { SessionStatus } from "../types";

const LABEL: Record<SessionStatus, string> = {
  live: "Live",
  finalizing: "Finalizing",
  done: "Done",
  failed: "Failed",
};

/**
 * `streaming` matters only for `live`: a live session whose recorder stopped sending audio
 * shows as Offline until the recorder reconnects or the server ends it.
 */
export function StatusChip({ status, streaming }: { status: SessionStatus; streaming: boolean }) {
  if (status === "live" && !streaming)
    return (
      <span
        class="chip chip-offline"
        data-testid="status-chip"
        title="No audio is streaming. The session resumes if the recorder reconnects, or ends on its own after a while."
      >
        <span class="dot dot-hollow" aria-hidden="true" />
        Offline
      </span>
    );
  return (
    <span class={`chip chip-${status}`} data-testid="status-chip">
      {status === "live" && <span class="dot" aria-hidden="true" />}
      {status === "failed" && <span aria-hidden="true">✕ </span>}
      {status === "done" && <span aria-hidden="true">✓ </span>}
      {LABEL[status]}
    </span>
  );
}
