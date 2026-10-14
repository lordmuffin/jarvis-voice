import { describe, expect, it } from "vitest";
import { followStep, NEAR_BOTTOM_PX } from "../src/follow";

const el = (scrollTop: number, clientHeight: number, scrollHeight: number) => ({ scrollTop, clientHeight, scrollHeight });

describe("followStep", () => {
  it("follows while the reader is at the bottom", () => {
    const s = followStep({ following: true, pausedAt: null }, el(900, 100, 1000), 10);
    expect(s.following).toBe(true);
    expect(s.newCount).toBe(0);
  });

  it("pauses when the reader scrolls up, remembering how many entries there were", () => {
    const s = followStep({ following: true, pausedAt: null }, el(500, 100, 1000), 10);
    expect(s.following).toBe(false);
    expect(s.pausedAt).toBe(10);
  });

  it("counts entries that arrived since the pause", () => {
    const paused = { following: false, pausedAt: 10 };
    expect(followStep(paused, el(500, 100, 1200), 13).newCount).toBe(3);
  });

  it("resumes on scrolling back to the bottom", () => {
    const s = followStep({ following: false, pausedAt: 10 }, el(1100, 100, 1200), 13);
    expect(s).toEqual({ following: true, pausedAt: null, newCount: 0 });
  });

  it("treats a few pixels above the bottom as the bottom", () => {
    const s = followStep({ following: true, pausedAt: null }, el(1000 - NEAR_BOTTOM_PX + 1, 100, 1100), 5);
    expect(s.following).toBe(true);
  });

  it("does not pause just because content grew under a reader who is following", () => {
    // New lines made scrollHeight larger, but the scroller has not moved yet: still following.
    const s = followStep({ following: true, pausedAt: null }, el(900, 100, 1300), 12, { programmatic: true });
    expect(s.following).toBe(true);
  });
});
