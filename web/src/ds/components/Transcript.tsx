import type { ComponentChildren } from "preact";
import { cx } from "../classes";

export type Speaker = "me" | "them";

/** One line of a transcript: when, who, what. `draft` is not final; `target` is highlighted. */
export function Utterance({
  time,
  speaker,
  draft,
  target,
  children,
  ...rest
}: {
  time: string;
  speaker: Speaker;
  draft?: boolean;
  target?: boolean;
  children: ComponentChildren;
  "data-line"?: string;
  "data-testid"?: string;
}) {
  return (
    <div
      {...rest}
      class={cx(
        "i-utt i-utt--stack",
        speaker === "them" && "i-utt--them",
        draft && "i-utt--draft",
        target && "i-utt--target",
      )}
    >
      <div class="hd">
        <span class={cx("i-spk", speaker === "me" ? "i-spk--me" : "i-spk--them")}>
          {speaker === "me" ? "Me" : "Them"}
        </span>
        <span class="t">{time}</span>
      </div>
      <span class="x">{children}</span>
    </div>
  );
}

/** A flag the speaker dropped in the transcript. */
export function TranscriptMarker({ time, children, ...rest }: { time: string; children: ComponentChildren; "data-testid"?: string }) {
  return (
    <div {...rest} class="j-marker">
      <span aria-hidden="true">⚑ </span>
      {children}
      <span class="t"> · {time}</span>
    </div>
  );
}

/** Plays one channel's recording. */
export function Player({
  label,
  labelId,
  them,
  children,
  ...rest
}: {
  label: string;
  labelId: string;
  them?: boolean;
  children: ComponentChildren;
  "data-testid"?: string;
}) {
  return (
    <div {...rest} class={cx("i-player", them && "i-player--them")}>
      <span class="i-label" id={labelId}>
        {label}
      </span>
      {children}
    </div>
  );
}

export function Recordings({ children }: { children: ComponentChildren }) {
  return (
    <section class="d-recordings" aria-label="Recordings">
      {children}
    </section>
  );
}

/** The finished note's body. */
export function NoteBody({ children, ...rest }: { children: ComponentChildren; "data-testid"?: string }) {
  return (
    <article {...rest} class="j-note">
      {children}
    </article>
  );
}
