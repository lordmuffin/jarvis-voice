import type { ComponentChildren } from "preact";
import { cx } from "../classes";

type Kids = { children?: ComponentChildren; class?: string; "data-testid"?: string; "aria-label"?: string };

/** Blocks stacked 4px apart. */
export function Stack({ children, grow, ...rest }: Kids & { grow?: boolean }) {
  return (
    <div {...rest} class={cx("i-stack", grow && "d-grow", rest.class)}>
      {children}
    </div>
  );
}

/** A surface-coloured panel. */
export function Block({ children, ...rest }: Kids) {
  return (
    <div {...rest} class={cx("i-block", rest.class)}>
      {children}
    </div>
  );
}

/** A page section: children spaced 8px apart. */
export function Section({ children, ...rest }: Kids) {
  return (
    <section {...rest} class={cx("d-section", rest.class)}>
      {children}
    </section>
  );
}

/** A wrapping row of controls (toolbars, form actions). */
export function Bar({ children, ...rest }: Kids & { role?: "toolbar" }) {
  return (
    <div {...rest} class={cx("d-bar", rest.class)}>
      {children}
    </div>
  );
}

/** A heading line: title, status, meta, wrapping on narrow screens. */
export function Head({ children, ...rest }: Kids) {
  return (
    <div {...rest} class={cx("d-head", rest.class)}>
      {children}
    </div>
  );
}

export function Spacer() {
  return <span class="d-spacer" />;
}

export function Bullets({ children, ...rest }: Kids) {
  return (
    <ul {...rest} class={cx("d-bullets", rest.class)}>
      {children}
    </ul>
  );
}

/** Pushes a control to the end of a row on narrow screens. */
export function Actions({ children }: { children?: ComponentChildren }) {
  return <div class="d-acts">{children}</div>;
}

/** A list of rows. */
export function List({ children, ...rest }: Kids) {
  return (
    <ul {...rest} class={cx("d-list", rest.class)}>
      {children}
    </ul>
  );
}

/** A static row: main text, then actions that drop under it on phones. */
export function ListItem({
  children,
  withActions,
  ...rest
}: Kids & { withActions?: boolean }) {
  return (
    <li {...rest} class={cx("d-item", withActions && "d-item--acts", rest.class)}>
      {children}
    </li>
  );
}

export function ItemMain({ children }: { children?: ComponentChildren }) {
  return <div class="d-item-main">{children}</div>;
}

/** A row that is a link (a session in the list). */
export function RowLink({
  href,
  onClick,
  children,
  ...rest
}: {
  href: string;
  onClick: (e: MouseEvent) => void;
  children: ComponentChildren;
  "data-testid"?: string;
}) {
  return (
    <a {...rest} class="j-srow" href={href} onClick={onClick}>
      {children}
    </a>
  );
}

export function RowTitle({ children, none }: { children: ComponentChildren; none?: boolean }) {
  return <span class={cx("ti", none && "ti--none")}>{children}</span>;
}

export function RowMeta({ children }: { children: ComponentChildren }) {
  return <span class="mt">{children}</span>;
}

/** Monospace inline code. */
export function Code({ children }: { children: ComponentChildren }) {
  return <code class="d-mono">{children}</code>;
}

/** A block of preformatted text (a prompt). */
export function CodeBlock({ children, ...rest }: Kids) {
  return (
    <pre {...rest} class="j-prompt d-pre-box">
      {children}
    </pre>
  );
}

/** A form that sits on a surface and wraps its fields in a row; takes the full toolbar width. */
export function InlineForm({
  children,
  onSubmit,
  ...rest
}: {
  children: ComponentChildren;
  onSubmit: (e: Event) => void;
  "data-testid"?: string;
}) {
  return (
    <form {...rest} class="j-upload d-fullrow" onSubmit={onSubmit}>
      {children}
    </form>
  );
}

/** A form. `inline` lays fields out in a wrapping row; `narrow` caps the width. */
export function Form({
  children,
  onSubmit,
  inline,
  narrow,
  grow,
  elRef,
  ...rest
}: Kids & {
  onSubmit: (e: Event) => void;
  inline?: boolean;
  narrow?: boolean;
  grow?: boolean;
  elRef?: (el: HTMLFormElement | null) => void;
}) {
  return (
    <form
      {...rest}
      ref={elRef}
      onSubmit={onSubmit}
      class={cx(inline ? "d-bar" : narrow ? "d-form" : "d-section", grow && "d-grow", rest.class)}
    >
      {children}
    </form>
  );
}

/** Fields (and their button) side by side, wrapping on narrow screens. */
export function FormRow({ children }: { children: ComponentChildren }) {
  return <div class="d-form-row">{children}</div>;
}

/** A scrolling stack whose scroll position the page watches (the live transcript). */
export function ScrollStack({
  children,
  onScroll,
  elRef,
}: {
  children: ComponentChildren;
  onScroll: (e: Event) => void;
  elRef: (el: HTMLDivElement | null) => void;
}) {
  return (
    <div class="i-stack" ref={elRef} onScroll={onScroll}>
      {children}
    </div>
  );
}

/** A timestamp link into a transcript ("▶ 0:42"). */
export function SourceLink({
  href,
  title,
  onClick,
  children,
}: {
  href: string;
  title?: string;
  onClick?: (e: MouseEvent) => void;
  children: ComponentChildren;
}) {
  return (
    <a class="i-src" href={href} title={title} onClick={onClick}>
      {children}
    </a>
  );
}
