// @vitest-environment happy-dom
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UploadRecording } from "../src/views/UploadRecording";

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
    const mode = form.querySelector<HTMLSelectElement>("select[name=mode]")!;
    mode.value = "meeting";
    mode.dispatchEvent(new Event("change"));

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

  it("cancel puts the file back", async () => {
    render(h(UploadRecording, { onUploaded: vi.fn() }), root);
    await choose(new File(["abc"], "a.wav"));
    root.querySelector<HTMLButtonElement>("[data-testid=upload-cancel]")!.click();
    await settle();
    expect(root.querySelector("[data-testid=upload-form]")).toBeNull();
  });
});
