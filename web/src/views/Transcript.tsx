import { useEffect, useRef, useState } from "preact/hooks";
import { clock } from "../format";
import { hashTime } from "../workitems";
import type { Line } from "../reducer";
import type { Marker } from "../types";

type Item = { at: number; line: Line } | { at: number; marker: Marker };

export function Transcript({
  lines,
  markers = [],
  follow = false,
}: {
  lines: Line[];
  markers?: Marker[];
  /** Keep the newest line in view while the reader is at the bottom. */
  follow?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  // `#t=<ms>` (a work item's source link) highlights the line spoken then.
  const [target, setTarget] = useState<number | null>(() => hashTime(location.hash));

  useEffect(() => {
    const el = box.current;
    if (follow && el && atBottom.current && target === null) el.scrollTop = el.scrollHeight;
  }, [lines.length, follow]);

  useEffect(() => {
    const on = () => setTarget(hashTime(location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  // The line containing the target time, else the last one starting before it.
  const hit =
    target === null
      ? undefined
      : (lines.find((l) => l.start_ms <= target && target < l.end_ms) ??
        [...lines].reverse().find((l) => l.start_ms <= target));

  useEffect(() => {
    if (!hit) return;
    atBottom.current = false;
    box.current
      ?.querySelector(`[data-line="${CSS.escape(hit.id)}"]`)
      ?.scrollIntoView({ block: "center" });
  }, [hit?.id, target]);

  const items: Item[] = [
    ...lines.map((line) => ({ at: line.start_ms, line })),
    ...markers.map((marker) => ({ at: marker.t_ms, marker })),
  ].sort((a, b) => a.at - b.at);

  if (items.length === 0) return <p class="muted">Nothing yet.</p>;

  return (
    <div
      class="transcript"
      ref={box}
      onScroll={(e) => {
        const el = e.currentTarget as HTMLElement;
        atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      }}
    >
      {items.map((it) =>
        "marker" in it ? (
          <div class="marker" key={`m-${it.marker.t_ms}-${it.marker.label}`} data-testid="marker">
            <span aria-hidden="true">⚑ </span>
            {it.marker.label}
            <span class="time"> · {clock(it.marker.t_ms)}</span>
          </div>
        ) : (
          <div
            key={it.line.id}
            class={`line line-${it.line.speaker}${it.line.kind === "draft" ? " line-draft" : ""}${
              it.line.id === hit?.id ? " line-target" : ""
            }`}
            data-line={it.line.id}
            data-testid={it.line.kind === "draft" ? "draft" : "segment"}
          >
            <span class="time">{clock(it.line.start_ms)}</span>
            <div>
              <div class="who">{it.line.speaker === "me" ? "Me" : "Them"}</div>
              <div class="text">{it.line.text}</div>
            </div>
          </div>
        ),
      )}
    </div>
  );
}
