import { useEffect, useState } from "preact/hooks";

/** `value`, but only after it has stopped changing for `ms` (a search box that doesn't fire per key). */
export function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}
