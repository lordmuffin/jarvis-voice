import { useEffect, useState } from "preact/hooks";
import { ApiError, getProcessing, getSession, processSession } from "../api";
import { Button, Pill, Reason } from "../ds/components";
import { processingView } from "../processing";
import type { Processing, SessionDetail } from "../types";

/**
 * "Transcribe & summarize" and what the session's jobs are doing. While one runs (started here or
 * by the server on its own: an upload, an ending session, a regeneration) the button waits and the
 * state is polled; once idle, the session is loaded again and handed to `onChanged`.
 */
export function ProcessControl({
  detail,
  onChanged,
  pollMs = 2000,
}: {
  detail: SessionDetail;
  onChanged: (d: SessionDetail) => void;
  pollMs?: number;
}) {
  const [processing, setProcessing] = useState<Processing | undefined>(detail.processing);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setProcessing(detail.processing), [detail]);

  const view = processingView(processing);
  useEffect(() => {
    if (!view.busy) return;
    let stopped = false;
    const timer = setInterval(async () => {
      try {
        const p = await getProcessing(detail.id);
        if (stopped) return;
        if (processingView(p).busy) return setProcessing(p);
        stopped = true;
        clearInterval(timer);
        const d = await getSession(detail.id);
        setProcessing(p);
        onChanged(d);
      } catch {
        // A dropped poll: try again on the next tick.
      }
    }, pollMs);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [view.busy, detail.id]);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      setProcessing((await processSession(detail.id)).processing);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError(e instanceof ApiError ? e.message : "Couldn't start. Try again.");
    } finally {
      setStarting(false);
    }
  }

  if (!detail.can_regenerate && !view.label) return null;
  const why = view.busy ? `Already ${view.label === "Summarizing…" ? "summarizing" : "transcribing"}.` : null;
  return (
    <div class="i-row">
      {detail.can_regenerate && (
        <Button
          variant="secondary"
          size="sm"
          disabled={view.busy || starting}
          title={why ?? "Transcribe the recording again, then write the note again"}
          aria-describedby={why ? "process-why" : undefined}
          onClick={start}
        >
          Transcribe &amp; summarize
        </Button>
      )}
      {view.label && (
        <Pill tone={view.tone ?? "neutral"} data-testid="processing">
          {view.busy ? <span class="i-spin i-chip-spin" aria-hidden="true" /> : <span aria-hidden="true">✕ </span>}
          {view.label}
        </Pill>
      )}
      {why && <Reason id="process-why">{why}</Reason>}
      {error && <Reason>{error}</Reason>}
    </div>
  );
}
