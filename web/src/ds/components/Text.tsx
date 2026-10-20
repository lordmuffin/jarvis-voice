import type { ComponentChildren, HTMLAttributes } from "preact";
import { cx } from "../classes";

export type TextSize = "stat" | "display" | "title" | "body-lg" | "body" | "caption" | "meta" | "label" | "mono";
export type TextTone = "default" | "muted" | "error" | "warning" | "ok" | "accent";

const SIZE: Record<TextSize, string> = {
  stat: "t-stat",
  display: "t-display",
  title: "t-title",
  "body-lg": "t-body-lg",
  body: "t-body",
  caption: "t-caption",
  meta: "t-meta",
  label: "t-label",
  mono: "t-mono",
};

export function Text({
  as: Tag = "span",
  size,
  tone = "default",
  strong,
  pre,
  mono,
  wrap,
  prose,
  class: c,
  children,
  ...rest
}: {
  as?: "span" | "p" | "div" | "h1" | "h2" | "h3";
  size?: TextSize;
  tone?: TextTone;
  strong?: boolean;
  /** Keep line breaks in the text. */
  pre?: boolean;
  mono?: boolean;
  /** Break long unbroken text (file names) instead of overflowing. */
  wrap?: boolean;
  /** Cap the line length for running text. */
  prose?: boolean;
  class?: string;
  children?: ComponentChildren;
} & Omit<HTMLAttributes<HTMLElement>, "size" | "class">) {
  const cls = cx(
    size && SIZE[size],
    tone !== "default" && `d-tone-${tone}`,
    strong && "t-strong",
    pre && "d-pre",
    mono && "d-mono",
    wrap && "d-wrap",
    prose && "d-note",
    c,
  );
  return (
    <Tag {...(rest as object)} class={cls || undefined}>
      {children}
    </Tag>
  );
}

/** A short reason beside a disabled or flagged control ("⚑ Close the pull request first."). */
export function Reason({ children, id }: { children: ComponentChildren; id?: string }) {
  return (
    <span class="i-reason" id={id}>
      {children}
    </span>
  );
}

/** A page heading. */
export function PageTitle({ children, ...rest }: { children: ComponentChildren; "data-testid"?: string }) {
  return (
    <h1 class="t-title" {...rest}>
      {children}
    </h1>
  );
}

/** A section heading in the small-caps label style. */
export function SectionLabel({ children, as: Tag = "h2" }: { children: ComponentChildren; as?: "h2" | "h3" }) {
  return <Tag class="t-label">{children}</Tag>;
}
