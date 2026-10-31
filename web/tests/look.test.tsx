// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LOOK_DELAY_MS, SETTLE_MS, useLookMarker } from "../src/look";

let seen = -1;
let seenWall = -1;
function Probe({ streamMs }: { streamMs: number }) {
  const look = useLookMarker(streamMs);
  seen = look.streamMs;
  seenWall = look.wallMs;
  return null;
}
const show = (streamMs: number) => render(<Probe streamMs={streamMs} />, document.body);
/** Open the page at `streamMs` and let the catching-up window pass. */
const open = async (streamMs: number) => {
  await act(() => {
    show(streamMs);
  });
  await act(() => {
    vi.advanceTimersByTime(SETTLE_MS);
  });
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
});
afterEach(() => {
  render(null, document.body);
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useLookMarker", () => {
  it("starts at the stream time when the page opened", async () => {
    await act(() => {
      show(5000);
    });
    expect(seen).toBe(5000);
  });

  it("follows the stream during the first seconds, while the page is still catching up", async () => {
    await act(() => {
      show(0);
    });
    await act(() => {
      show(4000);
    });
    expect(seen).toBe(4000);
    await act(() => {
      vi.advanceTimersByTime(SETTLE_MS);
    });
    await act(() => {
      show(9000);
    });
    expect(seen).toBe(4000);
  });

  it("does not move just because time passes while you watch passively", async () => {
    await open(5000);
    await act(() => {
      show(20000);
    });
    await act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(seen).toBe(5000);
  });

  it("moves to now 2 s after you interact with the page", async () => {
    await open(5000);
    await act(() => {
      show(20000);
    });
    await act(() => {
      window.dispatchEvent(new Event("pointerdown"));
    });
    await act(() => {
      vi.advanceTimersByTime(LOOK_DELAY_MS - 1);
    });
    expect(seen).toBe(5000);
    await act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(seen).toBe(20000);
  });

  it("uses the latest stream time when the 2 s are up", async () => {
    await open(5000);
    await act(() => {
      window.dispatchEvent(new Event("keydown"));
    });
    await act(() => {
      show(9000);
    });
    await act(() => {
      vi.advanceTimersByTime(LOOK_DELAY_MS);
    });
    expect(seen).toBe(9000);
  });

  it("remembers the wall clock of the look too, to compare with when findings arrived", async () => {
    await open(5000);
    const opened = seenWall;
    await act(() => {
      vi.advanceTimersByTime(10_000);
    });
    await act(() => {
      window.dispatchEvent(new Event("pointerdown"));
    });
    await act(() => {
      vi.advanceTimersByTime(LOOK_DELAY_MS);
    });
    expect(seenWall).toBeGreaterThan(opened);
  });

  it("does not move while the tab is hidden", async () => {
    await open(5000);
    await act(() => {
      show(9000);
    });
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    await act(() => {
      window.dispatchEvent(new Event("pointerdown"));
    });
    await act(() => {
      vi.advanceTimersByTime(LOOK_DELAY_MS * 2);
    });
    expect(seen).toBe(5000);
  });

  it("moves 2 s after the tab comes back into view", async () => {
    await open(5000);
    await act(() => {
      show(9000);
    });
    await act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await act(() => {
      vi.advanceTimersByTime(LOOK_DELAY_MS);
    });
    expect(seen).toBe(9000);
  });
});
