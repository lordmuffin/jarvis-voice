// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";
import { dismissSuggestion, loadDismissed, restoreSuggestion } from "../src/dismissals";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("suggestion dismissals", () => {
  it("start empty", () => {
    expect(loadDismissed("s1").size).toBe(0);
  });

  it("are remembered per session", () => {
    dismissSuggestion("s1", "q1");
    dismissSuggestion("s2", "q9");
    expect([...loadDismissed("s1")]).toEqual(["q1"]);
    expect([...loadDismissed("s2")]).toEqual(["q9"]);
  });

  it("survive a reload (a fresh read of storage)", () => {
    dismissSuggestion("s1", "q1");
    dismissSuggestion("s1", "q2");
    expect(loadDismissed("s1")).toEqual(new Set(["q1", "q2"]));
  });

  it("can be undone", () => {
    dismissSuggestion("s1", "q1");
    restoreSuggestion("s1", "q1");
    expect(loadDismissed("s1").size).toBe(0);
  });

  it("ignores a corrupt saved value", () => {
    localStorage.setItem("jarvis-live:dismissed:s1", "{not json");
    expect(loadDismissed("s1").size).toBe(0);
    localStorage.setItem("jarvis-live:dismissed:s1", '{"a":1}');
    expect(loadDismissed("s1").size).toBe(0);
  });

  it("keeps working when storage throws (the dismissal just won't survive a reload)", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => loadDismissed("s1")).not.toThrow();
    expect(() => dismissSuggestion("s1", "q1")).not.toThrow();
    expect(() => restoreSuggestion("s1", "q1")).not.toThrow();
  });

  it("does not grow without bound", () => {
    for (let i = 0; i < 300; i++) dismissSuggestion("s1", `q${i}`);
    const kept = loadDismissed("s1");
    expect(kept.size).toBeLessThanOrEqual(200);
    expect(kept.has("q299")).toBe(true);
  });
});
