import { RowLink } from "../ds/components";
import { clock, when } from "../format";
import type { SessionSummary, Status } from "../types";

/**
 * The planned "pinned live card": a live session at the top of Sessions, with the LIVE pill,
 * how long it has run, and tier and lag. Hidden until `PLANNED.pinnedLive` is turned on.
 */
export function PinnedLiveRow({
  session: s,
  elapsedMs,
  status,
  onOpen,
}: {
  session: SessionSummary;
  elapsedMs: number;
  status: Status | null;
  onOpen: (path: string) => void;
}) {
  const path = `/sessions/${s.id}`;
  return (
    <RowLink
      pinned
      href={path}
      data-testid="pinned-live"
      onClick={(e) => {
        e.preventDefault();
        onOpen(path);
      }}
    >
      <span class="i-live" role="status" aria-label={`Recording, ${clock(elapsedMs)}`}>
        <span class="i-dot i-breathe" aria-hidden="true" />
        <span class="lbl">LIVE</span>
        <span class="time">{clock(elapsedMs)}</span>
      </span>
      <span class="d-item-main">
        <span class="ti">{s.title?.trim() || "Untitled session"}</span>
        <span class="mt">
          {s.mode === "meeting" ? "Meeting" : "Solo"} · started {when(s.started_at)}
          {status && (
            <>
              {" · "}
              <span class="t-mono">
                {status.stt_tier ?? "no STT"} · lag {status.lag_ms} ms
              </span>
            </>
          )}
        </span>
      </span>
      <span class="t-meta">Open ▶</span>
    </RowLink>
  );
}
