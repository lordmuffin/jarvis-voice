// @vitest-environment happy-dom
import { render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NameSpeaker } from "../src/jarvis/NameSpeaker";
import type { SpeakerInfo } from "../src/types";

const unnamed: SpeakerInfo = { id: "spk_mic_1", label: null, enrolled: false, is_owner: false, ordinal: 2 };

let root: HTMLElement;
const input = () => root.querySelector<HTMLInputElement>("input.i-input")!;
const me = () => root.querySelector<HTMLInputElement>("input[type=checkbox]")!;
const button = (text: string) => [...root.querySelectorAll("button")].find((b) => b.textContent === text)!;
const flush = () => new Promise((r) => setTimeout(r, 0));
/** Preact renders on the next tick, so a change is only visible after one. */
const type = async (el: HTMLInputElement, value: string) => {
  el.value = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  await flush();
};

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

describe("NameSpeaker", () => {
  const open = (speaker = unnamed, onSaved = vi.fn(), onClose = vi.fn()) => {
    render(<NameSpeaker sessionId="s1" speaker={speaker} onSaved={onSaved} onClose={onClose} />, root);
    return { onSaved, onClose };
  };

  it("asks who an unnamed voice is, and cannot save a blank name", async () => {
    open();
    expect(root.querySelector("h2")!.textContent).toContain("Speaker 2");
    expect(input().value).toBe("");
    expect(button("Save").disabled).toBe(true);
    await type(input(), "   ");
    expect(button("Save").disabled).toBe(true);
    await type(input(), "Dana");
    expect(button("Save").disabled).toBe(false);
  });

  it("starts from the current name when renaming", () => {
    open({ ...unnamed, label: "Dana", enrolled: true });
    expect(input().value).toBe("Dana");
  });

  it("saves the name, with whether it is me, then reports back", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    globalThis.fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push({ url, init });
      return json({ ...unnamed, label: "Andrew", enrolled: true, is_owner: true });
    }) as unknown as typeof fetch;
    const { onSaved, onClose } = open();
    await type(input(), "  Andrew ");
    me().click();
    await flush();
    button("Save").click();
    await flush();
    expect(calls[0]).toMatchObject({
      url: "/v1/sessions/s1/speakers/spk_mic_1",
      init: { method: "PUT", body: JSON.stringify({ label: "Andrew", is_owner: true }) },
    });
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("stays open and says so when the server refuses", async () => {
    globalThis.fetch = vi.fn(async () => json({ detail: "Speaker not found." }, 404)) as unknown as typeof fetch;
    const { onSaved, onClose } = open();
    await type(input(), "Dana");
    button("Save").click();
    await flush();
    expect(onSaved).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(root.querySelector("[role=alert]")!.textContent).toContain("Speaker not found.");
  });

  it("closes without saving on Cancel", () => {
    globalThis.fetch = vi.fn() as unknown as typeof fetch;
    const { onClose } = open();
    button("Cancel").click();
    expect(onClose).toHaveBeenCalled();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
