import type { ConnectionState } from "../reducer";
import type { Status } from "../types";

const LABEL: Record<ConnectionState, string> = {
  idle: "Idle",
  connecting: "Connecting…",
  open: "Connected",
  closed: "Reconnecting…",
};

/**
 * The link to the server: state chip, then the tier and lag (quiet), and a warning when the
 * copilot is offline. `degraded` and `queuedMs` are for server fields that don't exist yet
 * (a fallback tier, audio queued while reconnecting); nothing passes them today.
 */
export function ConnectionChip({
  connection,
  status,
  copilotOffline,
  degraded,
  queuedMs,
}: {
  connection: ConnectionState;
  status: Status | null;
  copilotOffline?: boolean;
  degraded?: string;
  queuedMs?: number;
}) {
  const ok = connection === "open";
  const busy = connection === "connecting" || connection === "closed";
  const warn = connection === "closed" || degraded !== undefined;
  const tone = warn ? "i-chip--progress" : ok ? "i-chip--live" : "";
  const label =
    degraded !== undefined
      ? `⚑ Degraded · ${degraded}`
      : connection === "closed" && queuedMs !== undefined
        ? `Reconnecting… · ${(queuedMs / 1000).toFixed(1)} s queued`
        : LABEL[connection];
  return (
    <span class="d-stat">
      <span class={`i-chip ${tone}`} role="status" data-testid="connection">
        {busy && <span class="i-spin i-chip-spin" aria-hidden="true" />}
        {ok && degraded === undefined && <span class="i-dot" aria-hidden="true" />}
        {label}
      </span>
      {status && (
        <span class="t-meta t-2">
          <span class="t-mono">
            {status.stt_tier ?? "no STT"} · lag {status.lag_ms} ms
          </span>
          {copilotOffline && <span class="d-tone-warning"> · copilot offline</span>}
        </span>
      )}
    </span>
  );
}
