import type { ComponentChildren } from "preact";

/** Two panes side by side (wide screens). */
export function PaneGrid({ children }: { children: ComponentChildren }) {
  return <div class="d-panes">{children}</div>;
}

/** One pane: a heading above a body. Without `label` it is a bare column (tab layout). */
export function Pane({
  label,
  heading,
  children,
}: {
  label?: string;
  heading?: ComponentChildren;
  children: ComponentChildren;
}) {
  return (
    <section class="d-pane" aria-label={label}>
      {heading}
      {children}
    </section>
  );
}

export function PaneBody({
  children,
  ...rest
}: {
  children: ComponentChildren;
  role?: "tabpanel";
  id?: string;
  "aria-labelledby"?: string;
}) {
  return (
    <div {...rest} class="d-pane-body">
      {children}
    </div>
  );
}

/** The tab strip above a single pane on narrow screens. */
export function TabBar({ children }: { children: ComponentChildren }) {
  return <div class="d-seg-bar">{children}</div>;
}
