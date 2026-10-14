import { useEffect, useRef, useState } from "preact/hooks";
import { EmptyState, ScrollStack, TranscriptMarker, Utterance } from "../ds/components";
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

  if (items.length === 0) return <EmptyState>Nothing yet.</EmptyState>;

  return (
    <ScrollStack
      elRef={(el) => {
        box.current = el;
      }}
      onScroll={(e) => {
        const el = e.currentTarget as HTMLElement;
        atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      }}
    >
      {items.map((it) =>
        "marker" in it ? (
          <TranscriptMarker
            key={`m-${it.marker.t_ms}-${it.marker.label}`}
            time={clock(it.marker.t_ms)}
            data-testid="marker"
          >
            {it.marker.label}
          </TranscriptMarker>
        ) : (
          <Utterance
            key={it.line.id}
            speaker={it.line.speaker}
            time={clock(it.line.start_ms)}
            draft={it.line.kind === "draft"}
            target={it.line.id === hit?.id}
            data-line={it.line.id}
            data-testid={it.line.kind === "draft" ? "draft" : "segment"}
          >
            {it.line.text}
          </Utterance>
        ),
      )}
    </ScrollStack>
  );
}
