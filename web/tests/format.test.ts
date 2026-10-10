import { describe, expect, it } from "vitest";
import { ApiError, clearToken, loadToken, saveToken } from "../src/api";
import { clock, matchesTitle, titleOf } from "../src/format";
import { parseInline, parseMarkdown } from "../src/markdown";
import { parseRoute } from "../src/route";

describe("format", () => {
  it("formats stream time", () => {
    expect(clock(0)).toBe("0:00");
    expect(clock(65_400)).toBe("1:05");
    expect(clock(3_725_000)).toBe("1:02:05");
  });

  it("searches by title, case-insensitively, treating null as Untitled", () => {
    expect(matchesTitle({ title: "Weekly Sync" }, "sync")).toBe(true);
    expect(matchesTitle({ title: "Weekly Sync" }, "  ")).toBe(true);
    expect(matchesTitle({ title: "Weekly Sync" }, "budget")).toBe(false);
    expect(titleOf({ title: null })).toBe("Untitled session");
    expect(matchesTitle({ title: null }, "untitled")).toBe(true);
  });
});

describe("routes", () => {
  it("parses session paths", () => {
    expect(parseRoute("/")).toEqual({ name: "sessions" });
    expect(parseRoute("/sessions/abc-123")).toEqual({ name: "session", id: "abc-123" });
    expect(parseRoute("/nope")).toEqual({ name: "sessions" });
  });
});

describe("token storage", () => {
  it("round-trips through localStorage", () => {
    const mem = new Map<string, string>();
    globalThis.localStorage = {
      getItem: (k: string) => mem.get(k) ?? null,
      setItem: (k: string, v: string) => void mem.set(k, v),
      removeItem: (k: string) => void mem.delete(k),
    } as Storage;
    expect(loadToken()).toBeNull();
    saveToken("abc");
    expect(loadToken()).toBe("abc");
    clearToken();
    expect(loadToken()).toBeNull();
  });

  it("survives a localStorage that throws", () => {
    const boom = () => {
      throw new Error("blocked");
    };
    globalThis.localStorage = { getItem: boom, setItem: boom, removeItem: boom } as unknown as Storage;
    expect(loadToken()).toBeNull();
    expect(() => saveToken("x")).not.toThrow();
    expect(() => clearToken()).not.toThrow();
  });

  it("ApiError carries the status", () => {
    expect(new ApiError(401, "no").status).toBe(401);
  });
});

describe("markdown", () => {
  it("parses inline syntax and never emits raw html", () => {
    expect(parseInline("a **b** `c` [[Note|Alias]] [x](https://e.com) <script>")).toEqual([
      { t: "text", v: "a " },
      { t: "bold", v: "b" },
      { t: "text", v: " " },
      { t: "code", v: "c" },
      { t: "text", v: " " },
      { t: "wiki", v: "Alias" },
      { t: "text", v: " " },
      { t: "link", v: "x", href: "https://e.com" },
      { t: "text", v: " <script>" },
    ]);
  });

  it("does not turn javascript: links into links", () => {
    expect(parseInline("[x](javascript:alert(1))")).toEqual([
      { t: "text", v: "[x](javascript:alert(1))" },
    ]);
  });

  it("renders a finalizer note and drops the transcript section", () => {
    const md = [
      "---",
      "type: live-session",
      "---",
      "",
      "# Weekly sync",
      "",
      "## Summary",
      "",
      "We shipped.",
      "",
      "## Actions",
      "",
      "- [ ] Send deck (owner: sam)",
      "- [x] Book room",
      "",
      "## Transcript",
      "",
      "[0:01] **Me:** transcript-line",
      "",
      "## After",
      "",
      "tail",
    ].join("\n");
    const blocks = parseMarkdown(md, { dropSections: ["Transcript"] });
    expect(blocks.map((b) => b.t)).toEqual(["h", "h", "p", "h", "ul", "h", "p"]);
    const ul = blocks.find((b) => b.t === "ul");
    expect(ul && ul.t === "ul" && ul.items.map((i) => i.checked)).toEqual([false, true]);
    expect(JSON.stringify(blocks)).not.toContain("transcript-line");
    expect(JSON.stringify(blocks)).not.toContain("live-session");
  });
});
