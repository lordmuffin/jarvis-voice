// @vitest-environment happy-dom
import { h, render } from "preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadHealth, releaseChannel, serverBuild } from "../src/store";
import { HeaderVersion, ServerVersion } from "../src/views/ServerVersion";
import { Brand } from "../src/ds/components";

const healthz = (body: unknown) =>
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })),
  );

afterEach(() => {
  vi.unstubAllGlobals();
  serverBuild.value = null;
  releaseChannel.value = null;
  render(null, document.body);
  document.body.innerHTML = "";
});

describe("server version", () => {
  it("loads the version and commit, and the channel, from /healthz", async () => {
    healthz({ status: "ok", environment: "lab", version: "0.16.0", commit: "abc1234" });
    await loadHealth();
    expect(serverBuild.value).toEqual({ version: "0.16.0", commit: "abc1234" });
    expect(releaseChannel.value).toBe("lab");
  });

  it("knows the live channel too, so live can say so", async () => {
    healthz({ status: "ok", environment: "live", version: "0.16.0", commit: "abc1234" });
    await loadHealth();
    expect(releaseChannel.value).toBe("live");
  });

  it("knows nothing from an old server without the field", async () => {
    healthz({ status: "ok" });
    await loadHealth();
    expect(serverBuild.value).toBeNull();
  });

  // The family footer line, as Home and Sleep Dashboard print it: app, version and short commit.
  it("names the app, the version and the commit, in mono", () => {
    serverBuild.value = { version: "0.16.0", commit: "abc1234" };
    render(h(ServerVersion, {}), document.body);
    const el = document.querySelector("[data-testid=server-version]")!;
    expect(el.textContent).toBe("jarvis-live v0.16.0 · abc1234");
    expect(el.classList.contains("t-mono")).toBe(true);
  });

  it("leaves the commit out when the server does not know it", () => {
    serverBuild.value = { version: "0.16.0", commit: "" };
    render(h(ServerVersion, {}), document.body);
    expect(document.querySelector("[data-testid=server-version]")!.textContent).toBe(
      "jarvis-live v0.16.0",
    );
  });

  it("shows the bare version in the header", () => {
    serverBuild.value = { version: "0.16.0", commit: "abc1234" };
    render(h(HeaderVersion, {}), document.body);
    expect(document.querySelector("[data-testid=header-version]")!.textContent).toBe("v0.16.0");
  });

  it("shows no header version while it is unknown", () => {
    render(h(HeaderVersion, {}), document.body);
    expect(document.querySelector("[data-testid=header-version]")).toBeNull();
  });

  it("shows nothing while the version is unknown", () => {
    render(h(ServerVersion, {}), document.body);
    expect(document.querySelector("[data-testid=server-version]")).toBeNull();
    serverBuild.value = { version: "unknown", commit: "" };
    render(h(ServerVersion, {}), document.body);
    expect(document.querySelector("[data-testid=server-version]")).toBeNull();
  });
});

describe("release channel badge", () => {
  const tag = () => document.querySelector("[data-testid=release-channel]");
  const show = () => render(h(Brand, { channel: releaseChannel.value }), document.body);

  it("badges live as Live, in the live (red) style", () => {
    releaseChannel.value = "live";
    show();
    expect(tag()?.textContent).toBe("Live");
    expect(tag()?.classList.contains("i-badge--live")).toBe(true);
  });

  it("badges lab as Lab, not in the live style", () => {
    releaseChannel.value = "lab";
    show();
    expect(tag()?.textContent).toBe("Lab");
    expect(tag()?.classList.contains("i-badge--live")).toBe(false);
    expect(tag()?.classList.contains("i-badge--lab")).toBe(true);
  });

  // Any other channel (a dev server) is the neutral grey badge.
  it("badges any other channel in neither the live nor the lab style", () => {
    releaseChannel.value = "dev";
    show();
    expect(tag()?.textContent).toBe("dev");
    expect(tag()?.className).toBe("i-badge");
  });

  it("shows no badge while the channel is unknown", () => {
    show();
    expect(tag()).toBeNull();
  });

  it("says in the tooltip when this is not the live server", () => {
    releaseChannel.value = "lab";
    show();
    expect(tag()?.getAttribute("title")).toBe("This is the lab server, not live");
  });
});
