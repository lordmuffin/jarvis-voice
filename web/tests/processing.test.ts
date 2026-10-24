import { describe, expect, it } from "vitest";
import { processingView, showsProcessingWhileLive } from "../src/processing";
import type { Processing } from "../src/types";

const p = (over: Partial<Processing> = {}): Processing => ({
  transcription: "idle",
  summary: "idle",
  started_at: null,
  job_source: null,
  ...over,
});

describe("processingView", () => {
  it("is quiet when nothing runs, or for a server that does not say", () => {
    expect(processingView(p())).toEqual({ busy: false, label: null, tone: null });
    expect(processingView(undefined)).toEqual({ busy: false, label: null, tone: null });
  });

  it("says Transcribing… while the transcript is made, even with the note queued after it", () => {
    expect(processingView(p({ transcription: "running", summary: "queued", job_source: "upload" }))).toEqual({
      busy: true,
      label: "Transcribing…",
      tone: "progress",
    });
  });

  it("says how much of the recording is transcribed, when the server knows", () => {
    expect(processingView(p({ transcription: "running", job_source: "reprocess", progress: 0.426 }))).toEqual({
      busy: true,
      label: "Transcribing… 42%",
      tone: "progress",
    });
    // Rounded down: 100% only once it is all done.
    expect(processingView(p({ transcription: "running", job_source: "upload", progress: 0.999 })).label).toBe(
      "Transcribing… 99%",
    );
    expect(processingView(p({ transcription: "running", job_source: "live", progress: null })).label).toBe(
      "Transcribing…",
    );
  });

  it("says Summarizing… while the note is written", () => {
    expect(processingView(p({ summary: "running", job_source: "finalize" }))).toMatchObject({
      busy: true,
      label: "Summarizing…",
    });
  });

  it("reports a failed transcription without blocking a new run", () => {
    expect(processingView(p({ transcription: "failed" }))).toEqual({
      busy: false,
      label: "Transcription failed",
      tone: "failed",
    });
  });
});

describe("showsProcessingWhileLive", () => {
  it("follows an uploaded recording's transcription and an ending session", () => {
    expect(showsProcessingWhileLive("live", p({ transcription: "running", job_source: "upload" }))).toBe(true);
    expect(showsProcessingWhileLive("finalizing", p())).toBe(true);
  });

  it("stays out of a recording streamed live: its transcript is the progress", () => {
    expect(showsProcessingWhileLive("live", p({ transcription: "running", job_source: "live" }))).toBe(false);
    expect(showsProcessingWhileLive("live", undefined)).toBe(false);
  });
});
