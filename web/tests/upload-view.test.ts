// @vitest-environment happy-dom
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UploadRecording } from "../src/views/UploadRecording";
import { mp4 } from "./mp4";

let root: HTMLElement;
const settle = async () => {
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
};

function answer(status: number, body: unknown) {
  const f = vi.fn(async (_url: string | URL | Request, _init?: RequestInit) =>
    new Response(JSON.stringify(body), { status }),
  );
  globalThis.fetch = f as unknown as typeof fetch;
  return f;
}

/** Pick `file` in the hidden file input, as the browser's file chooser would. */
async function choose(file: File) {
  const input = root.querySelector<HTMLInputElement>("input[type=file]")!;
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  input.dispatchEvent(new Event("change"));
  await settle();
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

describe("upload a recording", () => {
  it("asks for title and mode once a file is chosen, then uploads and opens the session", async () => {
    const fetch = answer(202, { id: "s42" });
    const onUploaded = vi.fn();
    render(h(UploadRecording, { onUploaded }), root);
    expect(root.querySelector("[data-testid=upload-form]")).toBeNull();

    await choose(new File(["abc"], "Standup 10-12.m4a", { type: "audio/mp4" }));
    const form = root.querySelector<HTMLFormElement>("[data-testid=upload-form]")!;
    expect(form.textContent).toContain("Standup 10-12.m4a");
    const title = form.querySelector<HTMLInputElement>("input[name=title]")!;
    expect(title.placeholder).toBe("Standup 10-12");
    const meeting = [...form.querySelectorAll<HTMLButtonElement>("[role=radio]")].find((b) => b.textContent === "Meeting")!;
    meeting.click();
    await settle();

    form.dispatchEvent(new Event("submit", { cancelable: true }));
    await settle();

    expect(fetch).toHaveBeenCalledOnce();
    expect(String(fetch.mock.calls[0]![0])).toContain("mode=meeting");
    expect(onUploaded).toHaveBeenCalledWith("s42");
  });

  it("shows why the server refused the file and keeps it chosen", async () => {
    answer(415, { detail: "no audio could be read from the file" });
    const onUploaded = vi.fn();
    render(h(UploadRecording, { onUploaded }), root);
    await choose(new File(["abc"], "notes.txt"));

    root.querySelector("[data-testid=upload-form]")!.dispatchEvent(
      new Event("submit", { cancelable: true }),
    );
    await settle();

    expect(root.querySelector("[role=alert]")!.textContent).toContain(
      "no audio could be read from the file",
    );
    expect(root.querySelector("[data-testid=upload-form]")).not.toBeNull();
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it("sends only the audio of a video", async () => {
    const fetch = answer(202, { id: "s7" });
    render(h(UploadRecording, { onUploaded: vi.fn() }), root);
    const frames = [3, 5, 2, 4].map((n) => new Uint8Array(n).fill(7));
    await choose(new File([mp4({ audio: frames }) as BlobPart], "Offsite.mp4", { type: "video/mp4" }));

    root.querySelector("[data-testid=upload-form]")!.dispatchEvent(new Event("submit", { cancelable: true }));
    await settle();

    expect(String(fetch.mock.calls[0]![0])).toContain("filename=Offsite.aac");
    const body = fetch.mock.calls[0]![1]!.body as File;
    expect(body.size).toBe(14 + 4 * 7); // the frames and their ADTS headers, no video
  });

  it("uploads a video it can't take apart as it is, and says what to do if it's too big", async () => {
    const fetch = answer(413, { detail: "Recordings are limited to 500 MB." });
    render(h(UploadRecording, { onUploaded: vi.fn() }), root);
    await choose(new File(["not really mp4"], "screen.mp4", { type: "video/mp4" }));

    root.querySelector("[data-testid=upload-form]")!.dispatchEvent(new Event("submit", { cancelable: true }));
    await settle();

    expect(String(fetch.mock.calls[0]![0])).toContain("filename=screen.mp4");
    const alert = root.querySelector("[role=alert]")!.textContent!;
    expect(alert).toContain("limited to 500 MB");
    expect(alert).toContain("Save just the audio");
  });

  it("cancel puts the file back", async () => {
    render(h(UploadRecording, { onUploaded: vi.fn() }), root);
    await choose(new File(["abc"], "a.wav"));
    root.querySelector<HTMLButtonElement>("[data-testid=upload-cancel]")!.click();
    await settle();
    expect(root.querySelector("[data-testid=upload-form]")).toBeNull();
  });
});
