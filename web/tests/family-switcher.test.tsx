// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FamilySwitcher, Shell } from "../src/ds/components";
import { familyApps, loadFamilyApps } from "../src/store";
import type { FamilyApp } from "../src/types";

const APPS: FamilyApp[] = [
  { id: "home", name: "Home", url: "https://lab.apj.dev", available: true, current: false, glance_url: null },
  { id: "jarvis-live", name: "Jarvis Live", url: "https://live.lab.apj.dev", available: true, current: true, glance_url: null },
  { id: "sleep", name: "Sleep Dashboard", url: null, available: false, current: false, glance_url: null },
];

afterEach(() => {
  vi.unstubAllGlobals();
  familyApps.value = [];
  render(null, document.body);
  document.body.innerHTML = "";
});

const q = (sel: string) => document.querySelector(sel);
const qa = (sel: string) => [...document.querySelectorAll(sel)];

describe("FamilySwitcher", () => {
  it("renders nothing without apps, so a missing list means no switcher", () => {
    render(<FamilySwitcher apps={[]} channel="lab" />, document.body);
    expect(q(".i-switcher")).toBeNull();
  });

  it("is the ▦ button, closed until clicked", () => {
    render(<FamilySwitcher apps={APPS} channel="lab" />, document.body);
    const btn = q("button.i-switcher")!;
    expect(btn.getAttribute("aria-label")).toBe("Switch app");
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(q(".i-popover")).toBeNull();
  });

  it("opens a menu of links on the current channel, marking where you are", () => {
    render(<FamilySwitcher apps={APPS} channel="lab" />, document.body);
    act(() => (q("button.i-switcher") as HTMLElement).click());
    expect(q("button.i-switcher")!.getAttribute("aria-expanded")).toBe("true");
    expect(q(".i-popover")!.getAttribute("role")).toBe("menu");
    expect(q(".i-popover")!.textContent).toContain("LAB");
    const items = qa('.i-popover a[role="menuitem"]');
    expect(items.map((a) => a.getAttribute("href"))).toEqual(["https://lab.apj.dev", "https://live.lab.apj.dev"]);
    expect(items.map((a) => a.getAttribute("aria-current"))).toEqual([null, "page"]);
  });

  it("shows why an app with no copy on this channel cannot be opened", () => {
    render(<FamilySwitcher apps={APPS} channel="lab" />, document.body);
    act(() => (q("button.i-switcher") as HTMLElement).click());
    expect(qa('.i-popover [role="menuitem"]').length).toBe(3);
    expect(q(".i-popover .i-reason")!.textContent).toContain("Sleep Dashboard has no LAB copy");
    const off = qa('.i-popover [role="menuitem"]')[2]!;
    expect(off.getAttribute("href")).toBeNull();
    expect(off.getAttribute("aria-disabled")).toBe("true");
  });

  it("closes on Escape and gives focus back to the button", () => {
    render(<FamilySwitcher apps={APPS} channel="live" />, document.body);
    act(() => (q("button.i-switcher") as HTMLElement).click());
    act(() => {
      q(".i-popover")!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(q(".i-popover")).toBeNull();
    expect(document.activeElement).toBe(q("button.i-switcher"));
  });

  it("moves with the arrow keys", () => {
    render(<FamilySwitcher apps={APPS} channel="live" />, document.body);
    act(() => (q("button.i-switcher") as HTMLElement).click());
    const items = qa('.i-popover a[role="menuitem"]') as HTMLElement[];
    expect(document.activeElement).toBe(items[0]);
    act(() => {
      items[0]!.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
    });
    expect(document.activeElement).toBe(items[1]);
  });
});

describe("loadFamilyApps", () => {
  it("loads the apps for the switcher", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ apps: APPS }), { status: 200 })));
    await loadFamilyApps();
    expect(familyApps.value.map((a) => a.id)).toEqual(["home", "jarvis-live", "sleep"]);
  });

  it("fails closed to no switcher when the request fails", async () => {
    familyApps.value = APPS;
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 500 })));
    await loadFamilyApps();
    expect(familyApps.value).toEqual([]);
  });
});

describe("Shell narrow bottom bar", () => {
  it("repeats the tabs in an Instrument bottom bar, and hides the header tabs below 600px", () => {
    render(
      <Shell brand="b" nav={<a href="/x">X</a>} bottom={<a href="/x" aria-current="page">X</a>} tools="t">
        page
      </Shell>,
      document.body,
    );
    expect(q("header nav.i-nav")!.classList.contains("i-nav--wide")).toBe(true);
    const bar = q("nav.i-bottombar")!;
    expect(bar.getAttribute("aria-label")).toBe("Main");
    expect(bar.querySelector('[aria-current="page"]')).not.toBeNull();
  });

  it("has no bottom bar when the page gives no tabs", () => {
    render(<Shell brand="b" nav="n" tools="t">page</Shell>, document.body);
    expect(q("nav.i-bottombar")).toBeNull();
    expect(q("header nav.i-nav")!.classList.contains("i-nav--wide")).toBe(false);
  });
});
