// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";
import { streamUrl } from "../src/ws";

describe("streamUrl", () => {
  it("asks for speaker updates, so a viewer hears who is speaking", () => {
    const u = new URL(streamUrl("a b", "t/1"));
    expect(u.pathname).toBe("/v1/sessions/a%20b/stream");
    expect(u.searchParams.get("ticket")).toBe("t/1");
    expect(u.searchParams.get("features")).toBe("speaker_update");
  });
});
