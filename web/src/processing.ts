import type { Processing, SessionStatus } from "./types";

export interface ProcessingView {
  /** A job is in flight: a new one would not start. */
  busy: boolean;
  label: string | null;
  tone: "progress" | "failed" | null;
}

/** What to show for a session's jobs. The note waits for the transcript, so transcribing comes first. */
export function processingView(p: Processing | undefined): ProcessingView {
  if (!p) return { busy: false, label: null, tone: null };
  const active = (x: string) => x === "running" || x === "queued";
  if (active(p.transcription)) {
    // Rounded down: 100% only once it is all done.
    const pct = p.progress == null ? "" : ` ${Math.floor(p.progress * 100)}%`;
    return { busy: true, label: `Transcribing…${pct}`, tone: "progress" };
  }
  if (active(p.summary)) return { busy: true, label: "Summarizing…", tone: "progress" };
  if (p.transcription === "failed") return { busy: false, label: "Transcription failed", tone: "failed" };
  return { busy: false, label: null, tone: null };
}

/**
 * Whether the live view shows the session's jobs: an ending (or re-run) session, and an uploaded
 * recording while it is transcribed. A recording streamed live shows its transcript instead.
 */
export function showsProcessingWhileLive(status: SessionStatus, p: Processing | undefined): boolean {
  return status === "finalizing" || p?.job_source === "upload";
}
