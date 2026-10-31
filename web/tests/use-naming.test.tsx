// @vitest-environment happy-dom
import { render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TranscriptLine } from "../src/jarvis/TranscriptLine";
import { useNaming } from "../src/jarvis/useNaming";
import { me } from "../src/store";
import type { Me, SpeakerInfo } from "../src/types";

const speakers: Record<string, SpeakerInfo> = {
  s1: { id: "s1", label: null, enrolled: false, is_owner: false, ordinal: 2 },
};
const line = {
  kind: "segment" as const,
  key: "k",
  id: "seg",
  sortMs: 0,
  start_ms: 0,
  end_ms: 1000,
  speaker: "them" as const,
  text: "hello",
  speaker_id: "s1",
  who: { text: "Speaker 2", kind: "unknown" as const },
};

function Harness({ owner, refresh }: { owner: string; refresh: () => void }) {
  const { onName, dialog } = useNaming("sess", owner, speakers, refresh);
  return (
    <div>
      <TranscriptLine line={line} onName={onName} />
      {dialog}
    </div>
  );
}

let root: HTMLElement;
const flush = () => new Promise((r) => setTimeout(r, 0));
const user = (id: string) => ({ id }) as Me;

beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
});
afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  me.value = "loading";
  vi.restoreAllMocks();
});

describe("useNaming", () => {
  it("lets the session's owner name a voice from its tag, in a dialog", async () => {
    me.value = user("u1");
    render(<Harness owner="u1" refresh={() => undefined} />, root);
    root.querySelector<HTMLElement>(".i-spk")!.click();
    await flush();
    expect(document.querySelector("[role=dialog] h2")!.textContent).toContain("Speaker 2");
  });

  it("does not offer it to anyone else, an admin looking at another's session included", async () => {
    me.value = { ...user("u2"), is_admin: true };
    render(<Harness owner="u1" refresh={() => undefined} />, root);
    expect(root.querySelector(".i-spk")!.getAttribute("role")).toBeNull();
    root.querySelector<HTMLElement>(".i-spk")!.click();
    await flush();
    expect(document.querySelector("[role=dialog]")).toBeNull();
  });

  it("reloads the session once the name is saved, and closes", async () => {
    me.value = user("u1");
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ ...speakers["s1"], label: "Dana" }), {
          headers: { "Content-Type": "application/json" },
        }),
    ) as unknown as typeof fetch;
    const refresh = vi.fn();
    render(<Harness owner="u1" refresh={refresh} />, root);
    root.querySelector<HTMLElement>(".i-spk")!.click();
    await flush();
    const input = document.querySelector<HTMLInputElement>("input.i-input")!;
    input.value = "Dana";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await flush();
    [...document.querySelectorAll("button")].find((b) => b.textContent === "Save")!.click();
    await flush();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(document.querySelector("[role=dialog]")).toBeNull();
  });
});
