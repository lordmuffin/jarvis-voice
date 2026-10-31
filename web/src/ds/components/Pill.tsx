import type { ComponentChildren } from "preact";
import { cx } from "../classes";

export type PillTone = "neutral" | "live" | "progress" | "done" | "failed";

/** A small uppercase tag. State tones always carry a word or glyph too, never colour alone. */
export function Pill({
  tone = "neutral",
  tag,
  children,
  ...rest
}: {
  tone?: PillTone;
  /** A small label beside a name (e.g. "Admin"). */
  tag?: boolean;
  children: ComponentChildren;
  title?: string;
  "data-testid"?: string;
}) {
  return (
    <span {...rest} class={cx("i-chip", tone !== "neutral" && `i-chip--${tone}`, tag && "d-tag")}>
      {children}
    </span>
  );
}
