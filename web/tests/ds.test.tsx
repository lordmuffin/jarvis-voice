// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  Banner,
  Button,
  ChannelBadge,
  Dialog,
  Segmented,
  Shell,
  ShowOnceCard,
  StatusChip,
} from "../src/ds/components";
import { setTheme, theme } from "../src/theme";
import { ThemeToggle } from "../src/views/ThemeToggle";

afterEach(() => {
  render(null, document.body);
  document.body.innerHTML = "";
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  theme.value = "system";
  vi.restoreAllMocks();
});

const q = (sel: string) => document.querySelector(sel);

describe("Shell", () => {
  it("puts the footer after the page and before the bottom bar, like every family app", () => {
    render(
      <Shell brand="B" nav="N" tools="T" bottom="Tabs" footer={<span>v1</span>}>
        page
      </Shell>,
      document.body,
    );
    const app = q(".d-app")!;
    const kids = [...app.children].map((el) => el.tagName.toLowerCase() + "." + el.classList[0]);
    expect(kids).toEqual(["header.i-header", "main.i-page", "footer.i-footer", "nav.i-bottombar"]);
    expect(q("footer.i-footer")!.textContent).toBe("Private. Self-hosted. Open source.v1");
  });
});

describe("Button", () => {
  it("is a secondary type=button by default, so it never submits a form by accident", () => {
    render(<Button>Go</Button>, document.body);
    const b = q("button")!;
    expect(b.getAttribute("type")).toBe("button");
    expect(b.className).toBe("i-btn i-btn--secondary");
  });

  it("takes a variant and a size", () => {
    render(<Button variant="primary" size="lg">Go</Button>, document.body);
    expect(q("button")!.className).toBe("i-btn i-btn--primary i-btn--lg");
  });
});

describe("StatusChip", () => {
  const chip = (status: Parameters<typeof StatusChip>[0]["status"], streaming = true) => {
    render(<StatusChip status={status} streaming={streaming} />, document.body);
    return q("[data-testid=status-chip]")!;
  };

  it("names every state in words, never colour alone", () => {
    expect(chip("live").textContent).toBe("Live");
    expect(chip("finalizing").textContent).toBe("Finalizing");
    expect(chip("done").textContent).toBe("✓ Done");
    expect(chip("failed").textContent).toBe("✕ Failed");
  });

  it("shows a live session that stopped streaming as Offline, hollow and not breathing", () => {
    const el = chip("live", false);
    expect(el.textContent).toBe("Offline");
    expect(el.querySelector(".i-dot--hollow")).not.toBeNull();
    expect(el.querySelector(".i-breathe")).toBeNull();
  });

  it("breathes only while live", () => {
    expect(chip("live").querySelector(".i-breathe")).not.toBeNull();
    expect(chip("done").querySelector(".i-breathe")).toBeNull();
  });
});

describe("ChannelBadge", () => {
  it("is Live, Lab or the raw name, and absent while unknown", () => {
    render(<ChannelBadge channel={null} />, document.body);
    expect(q("[data-testid=release-channel]")).toBeNull();
    render(<ChannelBadge channel="live" />, document.body);
    expect(q(".i-badge--live")?.textContent).toBe("Live");
    render(<ChannelBadge channel="lab" />, document.body);
    expect(q(".i-badge--lab")?.textContent).toBe("Lab");
  });
});

describe("Banner", () => {
  it("announces errors as alerts and the rest politely", () => {
    render(<Banner variant="error">Nope</Banner>, document.body);
    expect(q(".i-banner--error")?.getAttribute("role")).toBe("alert");
    render(<Banner variant="viewing" title="Viewing Sam's sessions.">x</Banner>, document.body);
    const el = q(".i-banner--viewing")!;
    expect(el.getAttribute("role")).toBe("status");
    expect(el.querySelector("b")?.textContent).toBe("Viewing Sam's sessions.");
  });
});

describe("Segmented", () => {
  it("marks the chosen option and reports a click", () => {
    const onChange = vi.fn();
    render(
      <Segmented
        label="Theme"
        value="light"
        options={[
          { id: "light", label: "Light" },
          { id: "dark", label: "Dark" },
        ]}
        onChange={onChange}
      />,
      document.body,
    );
    const [light, dark] = [...document.querySelectorAll("button")];
    expect(light!.getAttribute("aria-checked")).toBe("true");
    expect(dark!.getAttribute("aria-checked")).toBe("false");
    dark!.click();
    expect(onChange).toHaveBeenCalledWith("dark");
  });
});

describe("ThemeToggle", () => {
  it("sets data-theme, saves it, and System clears it", async () => {
    render(<ThemeToggle />, document.body);
    const btn = (name: string) =>
      [...document.querySelectorAll("button")].find((b) => b.textContent === name)!;
    await act(() => {
      btn("Dark").click();
    });
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(btn("Dark").getAttribute("aria-checked")).toBe("true");
    expect(localStorage.getItem("jarvis-live:theme")).toBe("dark");
    await act(() => {
      btn("System").click();
    });
    expect(document.documentElement.dataset.theme).toBeUndefined();
    setTheme("system");
  });
});

describe("Dialog", () => {
  const open = (onClose = vi.fn()) => {
    render(
      <Dialog
        title="Revoke “Mac”?"
        onClose={onClose}
        actions={
          <>
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="danger">Revoke Mac</Button>
          </>
        }
      >
        It stops working right away.
      </Dialog>,
      document.body,
    );
    return onClose;
  };

  it("is a labelled modal that starts on Cancel", async () => {
    open();
    const d = q("[role=dialog]")!;
    expect(d.getAttribute("aria-modal")).toBe("true");
    expect(document.getElementById(d.getAttribute("aria-labelledby")!)?.textContent).toBe("Revoke “Mac”?");
    await act(() => undefined);
    expect(document.activeElement?.textContent).toBe("Cancel");
  });

  it("closes on Escape and on a click on the scrim, not inside the dialog", () => {
    const onClose = open();
    q("[role=dialog]")!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(onClose).toHaveBeenCalledTimes(1);
    q("[role=dialog]")!.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    expect(onClose).toHaveBeenCalledTimes(1);
    q(".d-scrim")!.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("keeps Tab inside, wrapping from the last button to the first", async () => {
    open();
    await act(() => undefined);
    const [cancel, revoke] = [...document.querySelectorAll<HTMLElement>(".i-dialog-ft button")];
    revoke!.focus();
    const ev = new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true });
    q("[role=dialog]")!.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(cancel);
  });
});

describe("ShowOnceCard", () => {
  const card = (onDone = vi.fn()) => {
    render(
      <ShowOnceCard
        heading="Device token"
        intro="Work MacBook is ready."
        warning="It is shown only once."
        value="tok_secret"
        valueLabel="Device token"
        footnote="The token is never logged."
        onDone={onDone}
      />,
      document.body,
    );
    return onDone;
  };

  it("shows the secret in a read-only field, with the warning and footnote", () => {
    card();
    const input = q("input")!;
    expect(input.getAttribute("aria-label")).toBe("Device token");
    expect((input as HTMLInputElement).value).toBe("tok_secret");
    expect(input.hasAttribute("readonly")).toBe(true);
    expect(q("[data-testid=new-token]")?.textContent).toContain("It is shown only once.");
    expect(q("[data-testid=new-token]")?.textContent).toContain("The token is never logged.");
  });

  it("copies to the clipboard and says so; Done dismisses", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const onDone = card();
    const btn = (name: RegExp) => [...document.querySelectorAll("button")].find((b) => name.test(b.textContent!))!;
    await act(async () => {
      btn(/Copy/).click();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(writeText).toHaveBeenCalledWith("tok_secret");
    expect(q("[data-testid=new-token]")?.textContent).toContain("Copied to the clipboard.");
    btn(/Done/).click();
    expect(onDone).toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("tells you to copy by hand when the clipboard is blocked", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: async () => Promise.reject(new Error("no")) } });
    card();
    await act(async () => {
      [...document.querySelectorAll("button")].find((b) => /Copy/.test(b.textContent!))!.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(q("[data-testid=new-token]")?.textContent).toContain("copy it by hand");
    vi.unstubAllGlobals();
  });
});
