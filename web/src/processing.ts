import type { Processing } from "./types";

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
  if (active(p.transcription)) return { busy: true, label: "Transcribing…", tone: "progress" };
  if (active(p.summary)) return { busy: true, label: "Summarizing…", tone: "progress" };
  if (p.transcription === "failed") return { busy: false, label: "Transcription failed", tone: "failed" };
  return { busy: false, label: null, tone: null };
}
