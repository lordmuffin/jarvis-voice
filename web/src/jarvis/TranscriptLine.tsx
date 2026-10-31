import type { HTMLAttributes } from "preact";
import { useCallback, useRef } from "preact/hooks";
import { clock } from "../format";
import type { Line } from "../reducer";

const MIXED = "This line includes a change of speaker";

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
  onName,
}: {
  line: Line;
  /** The line a work item's source link points at. */
  target?: boolean;
  /** Narrow panes: who and when on one line above the text. */
  stack?: boolean;
  /** Offered on a tag whose voice is identified: name it (and so remember it). */
  onName?: (speakerId: string) => void;
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
  const w = line.who;
  const tagCls = [
    "i-spk",
    w
      ? w.kind === "me"
        ? "i-spk--me"
        : "i-spk--them"
      : `i-spk--${line.speaker}`,
    // An unnamed voice is outlined, a named one filled: never colour alone, the words differ too.
    w?.kind === "unknown" && "i-spk--draft",
    onName && line.speaker_id && "d-spk-edit",
  ]
    .filter(Boolean)
    .join(" ");
  const id = line.speaker_id;
  const naming: HTMLAttributes<HTMLSpanElement> =
    onName && id
      ? {
          role: "button",
          tabIndex: 0,
          onClick: () => onName(id),
          onKeyDown: (e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onName(id);
            }
          },
        }
      : {};
  const who = (
    <span
      class={tagCls}
      data-who={w?.kind}
      title={line.mixed ? MIXED : undefined}
      {...naming}
    >
      {w?.text ?? (line.speaker === "me" ? "Me" : "Them")}
    </span>
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
        {line.mixed && <span class="i-sr"> ({MIXED})</span>}
      </span>
    </div>
  );
}
