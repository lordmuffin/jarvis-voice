import type { ComponentChildren } from "preact";
import { cx } from "../classes";

/** A work item: a draft Claude Code prompt. */
export function WorkCard({
  state,
  children,
  ...rest
}: {
  state: "draft" | "approved" | "discarded" | "merged" | "done";
  children: ComponentChildren;
  "data-testid"?: string;
  "data-state"?: string;
}) {
  return (
    <article
      {...rest}
      class={cx("j-wi", state === "approved" && "j-wi--approved", (state === "discarded" || state === "merged" || state === "done") && "j-wi--retired")}
    >
      {children}
    </article>
  );
}

/** A Claude Code run on a work item. */
export function RunBox({ children, ...rest }: { children: ComponentChildren; "data-testid"?: string }) {
  return (
    <div {...rest} class="j-run">
      {children}
    </div>
  );
}

export function RunHead({ children }: { children: ComponentChildren }) {
  return <div class="hd">{children}</div>;
}

export function Quote({ children, ...rest }: { children: ComponentChildren; "data-testid"?: string }) {
  return (
    <blockquote {...rest}>
      {children}
    </blockquote>
  );
}

/** A "Related" card in the copilot: title and a two-line snippet. */
export function Related({ href, title, snippet }: { href?: string; title: string; snippet: string }) {
  const body = (
    <>
      <span class="d-tone-accent">{title}</span>
      <span class="snip">{snippet}</span>
    </>
  );
  return href ? (
    <a class="j-related" href={href}>
      {body}
    </a>
  ) : (
    <div class="j-related">{body}</div>
  );
}

/** A copilot item row (a suggestion, a decision). */
export function Item({
  glyph = "•",
  children,
  ...rest
}: {
  glyph?: string;
  children: ComponentChildren;
  "data-testid"?: string;
}) {
  return (
    <div {...rest} class="j-item">
      <span class="g" aria-hidden="true">
        {glyph}
      </span>
      {children}
    </div>
  );
}
