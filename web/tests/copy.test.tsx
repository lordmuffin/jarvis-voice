// @vitest-environment happy-dom
import { render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { actionsChecklist, noteBody } from "../src/copy";
import { CopyButton } from "../src/jarvis/CopyButton";

describe("what is copied", () => {
  it("the note without its frontmatter", () => {
    expect(noteBody("---\nsession_id: s1\ntags: [a]\n---\n\n# Title\n\nBody.\n")).toBe("# Title\n\nBody.\n");
    expect(noteBody("# No frontmatter\n")).toBe("# No frontmatter\n");
  });

  it("actions as a plain checklist, one per line", () => {
    expect(
      actionsChecklist([
        { id: "a", text: "Send the inventory", owner: "Me", due: "Fri" },
        { id: "b", text: "Book the room", owner: null },
      ]),
    ).toBe("- [ ] Send the inventory (owner: Me, due: Fri)\n- [ ] Book the room\n");
  });
});

let root: HTMLElement;
const settle = async () => {
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
};
function define(target: object, key: string, value: unknown) {
  Object.defineProperty(target, key, { value, configurable: true, writable: true });
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

describe("CopyButton", () => {
  it("copies its text and says so", async () => {
    const writeText = vi.fn(async () => undefined);
    define(navigator, "clipboard", { writeText });
    define(window, "isSecureContext", true);
    render(<CopyButton label="Copy Actions" text={() => "- [ ] x\n"} />, root);
    root.querySelector("button")!.click();
    await settle();
    expect(writeText).toHaveBeenCalledWith("- [ ] x\n");
    expect(root.querySelector("button")!.textContent).toBe("✓ Copied");
    expect(root.querySelector("[role=status]")!.textContent).toBe("Copied to the clipboard.");
  });
});
