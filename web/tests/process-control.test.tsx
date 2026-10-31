// @vitest-environment happy-dom
import { render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProcessControl } from "../src/jarvis/ProcessControl";
import type { Processing, SessionDetail } from "../src/types";

const IDLE: Processing = { transcription: "idle", summary: "idle", started_at: null, job_source: null };
const RUNNING: Processing = { ...IDLE, transcription: "running", summary: "queued", job_source: "reprocess" };

const detail = (over: Partial<SessionDetail> = {}): SessionDetail =>
  ({
    id: "s1",
    status: "done",
    can_regenerate: true,
    processing: IDLE,
    ...over,
  }) as SessionDetail;

let root: HTMLElement;
const button = () => [...root.querySelectorAll("button")].find((b) => b.textContent === "Transcribe & summarize");

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("ProcessControl", () => {
  it("offers the button when the server can run it", () => {
    render(<ProcessControl detail={detail()} onChanged={() => undefined} />, root);
    expect(button()!.disabled).toBe(false);
    render(<ProcessControl detail={detail({ can_regenerate: false })} onChanged={() => undefined} />, root);
    expect(button()).toBeUndefined();
  });

  it("waits, and says why, while a job the server started is running", () => {
    globalThis.fetch = vi.fn(async () => json(RUNNING)) as unknown as typeof fetch;
    render(<ProcessControl detail={detail({ processing: RUNNING })} onChanged={() => undefined} pollMs={60_000} />, root);
    expect(button()!.disabled).toBe(true);
    expect(button()!.title).toBe("Already transcribing.");
    expect(root.querySelector("[data-testid=processing]")!.textContent).toContain("Transcribing…");
  });

  it("starts a run, follows it until it is idle, then reloads the session", async () => {
    const done = detail({ title: "Again" } as Partial<SessionDetail>);
    const replies = [json({ started: true, processing: RUNNING }, 202), json(RUNNING), json(IDLE), json(done)];
    const calls: string[] = [];
    globalThis.fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push(`${init.method ?? "GET"} ${url}`);
      return replies.shift()!;
    }) as unknown as typeof fetch;
    const changed = vi.fn();
    render(<ProcessControl detail={detail()} onChanged={changed} pollMs={1} />, root);

    button()!.click();
    await vi.waitFor(() => expect(changed).toHaveBeenCalledWith(done));
    expect(calls).toEqual([
      "POST /v1/sessions/s1/process",
      "GET /v1/sessions/s1/processing",
      "GET /v1/sessions/s1/processing",
      "GET /v1/sessions/s1?include_deleted=true",
    ]);
  });
});
