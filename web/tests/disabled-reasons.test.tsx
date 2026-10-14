// @vitest-environment happy-dom
import { render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Devices } from "../src/views/Devices";

let root: HTMLElement;
const settle = async () => {
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
};

beforeEach(() => {
  globalThis.fetch = vi.fn(async () => new Response("[]", { status: 200 })) as unknown as typeof fetch;
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("disabled controls say why", () => {
  it("Add Device explains itself until a name is entered", async () => {
    render(<Devices />, root);
    await settle();
    const add = [...root.querySelectorAll("button")].find((b) => b.textContent === "Add Device")!;
    expect(add.disabled).toBe(true);
    expect(add.title).toBe("Enter a device name.");
    expect(root.textContent).toContain("Enter a device name.");
  });
});
