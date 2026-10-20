import { useRef } from "preact/hooks";
import { trackArrivals, type Arrivals } from "../feed";

import { SETTLE_MS } from "../look";

export { SETTLE_MS };

/**
 * When each finding or suggestion was first seen, in stream time. What is there when the page
 * opens (and the REST snapshot that follows the socket) is `initial`: placed, never "new".
 */
export function useArrivals(keys: string[], streamMs: number, now: () => number = Date.now): Arrivals {
  const mounted = useRef<number | null>(null);
  const arrivals = useRef<Arrivals>({});
  if (mounted.current === null) mounted.current = now();
  const initial = now() - mounted.current < SETTLE_MS;
  arrivals.current = trackArrivals(arrivals.current, keys, streamMs, initial);
  return arrivals.current;
}
