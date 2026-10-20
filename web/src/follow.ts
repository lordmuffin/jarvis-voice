import { useCallback, useLayoutEffect, useRef, useState } from "preact/hooks";

/** Within this many pixels of the bottom still counts as at the bottom. */
export const NEAR_BOTTOM_PX = 24;

export interface FollowState {
  following: boolean;
  /** How many feed entries there were when the reader scrolled up; null while following. */
  pausedAt: number | null;
}

export interface ScrollBox {
  scrollTop: number;
  clientHeight: number;
  scrollHeight: number;
}

/**
 * Pure step of the follow rule. Scrolling up pauses; scrolling back to the bottom resumes;
 * `newCount` is what arrived since the pause. `programmatic` is true when the content (not the
 * reader) changed, so growth under a follower never pauses it.
 */
export function followStep(
  state: FollowState,
  box: ScrollBox,
  entryCount: number,
  opts: { programmatic?: boolean } = {},
): FollowState & { newCount: number } {
  const distance = box.scrollHeight - box.scrollTop - box.clientHeight;
  if (distance <= NEAR_BOTTOM_PX) return { following: true, pausedAt: null, newCount: 0 };
  if (opts.programmatic && state.following) return { ...state, newCount: 0 };
  const pausedAt = state.pausedAt ?? entryCount;
  return { following: false, pausedAt, newCount: Math.max(0, entryCount - pausedAt) };
}

/**
 * Follow the bottom of a scrolling log. The element that scrolls is the one given to `setEl`
 * (the `role="log"` box itself, not a child inside it).
 */
export function useFollow(entryCount: number, enabled = true) {
  const el = useRef<HTMLElement | null>(null);
  const [state, setState] = useState<FollowState>({ following: true, pausedAt: null });
  const [newCount, setNewCount] = useState(0);

  const setEl = useCallback((node: HTMLElement | null) => {
    el.current = node;
  }, []);

  const apply = (next: FollowState & { newCount: number }) => {
    setState((s) => (s.following === next.following && s.pausedAt === next.pausedAt ? s : { following: next.following, pausedAt: next.pausedAt }));
    setNewCount(next.newCount);
  };

  const onScroll = () => {
    if (!enabled || !el.current) return;
    apply(followStep(state, el.current, entryCount));
  };

  // Content grew: stay glued to the bottom while following, otherwise count what is new.
  useLayoutEffect(() => {
    const node = el.current;
    if (!enabled || !node) return;
    if (state.following) node.scrollTop = node.scrollHeight;
    else apply(followStep(state, node, entryCount, { programmatic: true }));
  }, [entryCount, enabled]);

  const jump = () => {
    const node = el.current;
    if (node) node.scrollTop = node.scrollHeight;
    setState({ following: true, pausedAt: null });
    setNewCount(0);
  };

  return { setEl, onScroll, following: state.following, newCount, jump };
}
