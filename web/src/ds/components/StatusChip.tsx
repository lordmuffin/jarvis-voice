import type { SessionStatus } from "../../types";
import { Pill } from "./Pill";

const LABEL: Record<SessionStatus, string> = {
  live: "Live",
  finalizing: "Finalizing",
  done: "Done",
  failed: "Failed",
};

/**
 * A session's state. `streaming` matters only for `live`: a live session whose recorder stopped
 * sending audio shows as Offline until the recorder reconnects or the server ends it.
 */
export function StatusChip({
  status,
  streaming,
  paused,
}: {
  status: SessionStatus;
  streaming: boolean;
  /** Planned: the design keeps a slot for a paused session. Nothing sets it yet. */
  paused?: boolean;
}) {
  if (paused) return <Pill data-testid="status-chip">Paused</Pill>;
  if (status === "live" && !streaming)
    return (
      <Pill
        data-testid="status-chip"
        title="No audio is streaming. The session resumes if the recorder reconnects, or ends on its own after a while."
      >
        <span class="i-dot i-dot--hollow" aria-hidden="true" />
        Offline
      </Pill>
    );
  switch (status) {
    case "live":
      return (
        <Pill tone="live" data-testid="status-chip">
          <span class="i-dot i-breathe" aria-hidden="true" />
          {LABEL.live}
        </Pill>
      );
    case "finalizing":
      return (
        <Pill tone="progress" data-testid="status-chip">
          <span class="i-spin i-chip-spin" aria-hidden="true" />
          {LABEL.finalizing}
        </Pill>
      );
    case "done":
      return (
        <Pill tone="done" data-testid="status-chip">
          <span aria-hidden="true">✓ </span>
          {LABEL.done}
        </Pill>
      );
    case "failed":
      return (
        <Pill tone="failed" data-testid="status-chip">
          <span aria-hidden="true">✕ </span>
          {LABEL.failed}
        </Pill>
      );
  }
}
