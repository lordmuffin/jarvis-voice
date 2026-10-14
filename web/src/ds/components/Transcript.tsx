import type { ComponentChildren } from "preact";
import { cx } from "../classes";

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
