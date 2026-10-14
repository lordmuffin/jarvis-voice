// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";
import { applyStoredTheme, setTheme, theme, THEME_KEY } from "../src/theme";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  theme.value = "system";
});

describe("theme", () => {
  it("follows the system by default: no data-theme attribute", () => {
    applyStoredTheme();
    expect(theme.value).toBe("system");
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it("sets and saves an explicit choice", () => {
    setTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
    setTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("System removes the override", () => {
    setTheme("dark");
    setTheme("system");
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it("restores the saved choice", () => {
    localStorage.setItem(THEME_KEY, "dark");
    applyStoredTheme();
    expect(theme.value).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("ignores a garbage saved value", () => {
    localStorage.setItem(THEME_KEY, "neon");
    applyStoredTheme();
    expect(theme.value).toBe("system");
  });

  it("still works when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => applyStoredTheme()).not.toThrow();
    expect(() => setTheme("light")).not.toThrow();
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
