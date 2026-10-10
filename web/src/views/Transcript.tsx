import { useEffect, useRef } from "preact/hooks";
import { clock } from "../format";
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

  useEffect(() => {
    const el = box.current;
    if (follow && el && atBottom.current) el.scrollTop = el.scrollHeight;
  }, [lines.length, follow]);

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
            class={`line line-${it.line.speaker}${it.line.kind === "draft" ? " line-draft" : ""}`}
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
