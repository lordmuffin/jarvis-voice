import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "../src");

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (relative(SRC, p) === "instrument") return []; // vendored, managed: never ours to lint
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}

const styled = files(SRC).filter((f) => /\.(tsx?|css)$/.test(f));

describe("web/ takes its look from Instrument", () => {
  it("has files to check", () => {
    expect(styled.length).toBeGreaterThan(20);
  });

  it("has no hard-coded colours", () => {
    const bad = styled.filter((f) => /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/.test(readFileSync(f, "utf8")));
    expect(bad.map((f) => relative(SRC, f))).toEqual([]);
  });

  it("has no font stacks of its own", () => {
    const bad = styled.filter((f) => /font-family\s*:(?!\s*var\(--inst-)|font\s*:\s*[^;]*\b(px|em|rem)\b/.test(readFileSync(f, "utf8")));
    expect(bad.map((f) => relative(SRC, f))).toEqual([]);
  });

  it("has no inline styles", () => {
    const bad = styled.filter((f) => /\bstyle=["{]/.test(readFileSync(f, "utf8")));
    expect(bad.map((f) => relative(SRC, f))).toEqual([]);
  });

  it("keeps raw Instrument classes inside ds/", () => {
    const outside = files(SRC).filter((f) => /\.tsx$/.test(f) && !relative(SRC, f).startsWith("ds"));
    const bad = outside.filter((f) => /class=["{`][^"}`]*\b(i-|j-|d-)[a-z]/.test(readFileSync(f, "utf8")));
    expect(bad.map((f) => relative(SRC, f))).toEqual([]);
  });
});
