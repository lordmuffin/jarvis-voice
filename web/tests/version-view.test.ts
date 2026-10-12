// @vitest-environment happy-dom
import { h, render } from "preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadHealth, releaseChannel, serverBuild } from "../src/store";
import { ServerVersion } from "../src/views/ServerVersion";

const healthz = (body: unknown) =>
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })),
  );

afterEach(() => {
  vi.unstubAllGlobals();
  serverBuild.value = null;
  releaseChannel.value = null;
  document.body.innerHTML = "";
});

describe("server version", () => {
  it("loads the version and commit, and the channel, from /healthz", async () => {
    healthz({ status: "ok", environment: "lab", version: "0.16.0", commit: "abc1234" });
    await loadHealth();
    expect(serverBuild.value).toEqual({ version: "0.16.0", commit: "abc1234" });
    expect(releaseChannel.value).toBe("lab");
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
