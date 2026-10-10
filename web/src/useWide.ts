import { useEffect, useState } from "preact/hooks";

/** Two panes from 840 px (unfolded / desktop); tabs below. */
export const WIDE_QUERY = "(min-width: 840px)";

export function useWide(): boolean {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE_QUERY).matches);
  useEffect(() => {
    const mq = window.matchMedia(WIDE_QUERY);
    const on = () => setWide(mq.matches);
    mq.addEventListener("change", on);
    on();
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}
