import { useEffect, useRef, useState } from "preact/hooks";

/** How long the page has your attention before "what changed" resets, as in the glance spec. */
export const LOOK_DELAY_MS = 2000;

/** For this long after the page opens, it is still catching up: what arrives is "already there". */
export const SETTLE_MS = 3000;

const attentive = () =>
  document.visibilityState !== "hidden" && (typeof document.hasFocus === "function" ? document.hasFocus() : true);

/**
 * The stream time you last really looked at the feed. Everything the copilot found after it is
 * "new". It starts at the time the page opened and moves only after {@link LOOK_DELAY_MS} of
 * attention that follows an interaction or the tab coming back: a feed you merely leave open
 * keeps its new rows until you look.
 */
export interface Look {
  /** Stream time then, for "since you looked at 17:15". */
  streamMs: number;
  /** Wall clock then, to compare with when findings arrived. */
  wallMs: number;
}

export function useLookMarker(streamMs: number): Look {
  const [look, setLook] = useState<Look>({ streamMs, wallMs: Date.now() });
  const setLookedAt = (ms: number) => setLook({ streamMs: ms, wallMs: Date.now() });
  const latest = useRef(streamMs);
  latest.current = streamMs;
  const opened = useRef(Date.now());

  // While the page is still catching up, you have seen everything so far.
  useEffect(() => {
    if (Date.now() - opened.current < SETTLE_MS) setLookedAt(streamMs);
  }, [streamMs]);

  useEffect(() => {
    let timer: number | undefined;
    const arm = () => {
      window.clearTimeout(timer);
      if (!attentive()) return;
      timer = window.setTimeout(() => {
        if (attentive()) setLookedAt(latest.current);
      }, LOOK_DELAY_MS);
    };
    const events = ["pointerdown", "keydown", "wheel", "focus"] as const;
    for (const e of events) window.addEventListener(e, arm);
    document.addEventListener("visibilitychange", arm);
    return () => {
      window.clearTimeout(timer);
      for (const e of events) window.removeEventListener(e, arm);
      document.removeEventListener("visibilitychange", arm);
    };
  }, []);

  return look;
}
