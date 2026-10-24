import { describe, expect, it } from "vitest";
import { processingView } from "../src/processing";
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
