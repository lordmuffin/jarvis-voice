// @vitest-environment happy-dom
import { h, render } from "preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadHealth, releaseChannel, serverBuild } from "../src/store";
import { ServerVersion } from "../src/views/ServerVersion";
import { Wordmark } from "../src/views/Wordmark";

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

  it("shows the version, with the commit on hover", () => {
    serverBuild.value = { version: "0.16.0", commit: "abc1234" };
    render(h(ServerVersion, {}), document.body);
    const el = document.querySelector("[data-testid=server-version]")!;
    expect(el.textContent).toBe("v0.16.0");
    expect(el.getAttribute("title")).toBe("Server version 0.16.0 (commit abc1234)");
  });

  it("shows nothing while the version is unknown", () => {
    render(h(ServerVersion, {}), document.body);
    expect(document.querySelector("[data-testid=server-version]")).toBeNull();
    serverBuild.value = { version: "unknown", commit: "" };
    render(h(ServerVersion, {}), document.body);
    expect(document.querySelector("[data-testid=server-version]")).toBeNull();
  });
});

describe("release channel tag", () => {
  const tag = () => document.querySelector("[data-testid=release-channel]");

  it("tags live as live, in the live (red) style", () => {
    releaseChannel.value = "live";
    render(h(Wordmark, {}), document.body);
    expect(tag()?.textContent).toBe("live");
    expect(tag()?.classList.contains("release-channel--live")).toBe(true);
  });

  it("tags lab as lab, not in the live style", () => {
    releaseChannel.value = "lab";
    render(h(Wordmark, {}), document.body);
    expect(tag()?.textContent).toBe("lab");
    expect(tag()?.classList.contains("release-channel--live")).toBe(false);
    expect(tag()?.classList.contains("release-channel--lab")).toBe(true);
  });

  // design/README.md, Color: amber is lab's; any other channel (a dev server) is grey.
  it("tags any other channel in neither the live nor the lab style", () => {
    releaseChannel.value = "dev";
    render(h(Wordmark, {}), document.body);
    expect(tag()?.textContent).toBe("dev");
    expect(tag()?.className).toBe("release-channel");
  });

  it("shows no tag while the channel is unknown", () => {
    render(h(Wordmark, {}), document.body);
    expect(tag()).toBeNull();
  });
});
