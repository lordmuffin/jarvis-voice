import { useCallback, useRef } from "preact/hooks";
import { clock } from "../format";
import type { Line } from "../reducer";

/**
 * One transcript line: time, who (a text label, so it never relies on colour alone, plus the
 * coloured bar), what was said. A draft is italic with an outlined tag. The draft and its final
 * share one keyed row: the text is replaced in place and the row never gets shorter than the
 * draft was, so nothing below it jumps when the final lands.
 */
export function TranscriptLine({
  line,
  target,
  stack,
}: {
  line: Line;
  /** The line a work item's source link points at. */
  target?: boolean;
  /** Narrow panes: who and when on one line above the text. */
  stack?: boolean;
}) {
  const draft = line.kind === "draft";
  const peak = useRef(0);
  // Measured when the text or kind changes (not on every render of a long transcript).
  const hold = useCallback(
    (el: HTMLElement | null) => {
      if (!el) return;
      el.style.minHeight = "";
      const natural = el.offsetHeight;
      if (natural > peak.current) peak.current = natural;
      if (peak.current > natural) el.style.minHeight = `${peak.current}px`;
    },
    [line.text, line.kind],
  );
  const cls = [
    "i-utt",
    stack && "i-utt--stack",
    line.speaker === "them" && "i-utt--them",
    draft && "i-utt--draft",
    target && "i-utt--target",
  ]
    .filter(Boolean)
    .join(" ");
  const who = (
    <span class={`i-spk i-spk--${line.speaker}`}>{line.speaker === "me" ? "Me" : "Them"}</span>
  );
  const time = <span class="t">{clock(line.start_ms)}</span>;
  return (
    <div
      ref={hold}
      class={cls}
      data-line={line.id}
      data-key={line.key}
      data-testid={draft ? "draft" : "segment"}
      aria-current={target ? "true" : undefined}
    >
      {stack ? (
        <span class="hd">
          {who}
          {time}
        </span>
      ) : (
        <>
          {time}
          {who}
        </>
      )}
      <span class="x">
        {line.text}
        {draft && <span class="i-sr"> (draft)</span>}
      </span>
    </div>
  );
}
