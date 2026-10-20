import type { ComponentChildren } from "preact";

/** A data table in the family style; scrolls sideways on narrow screens. */
export function Table({ children, label }: { children: ComponentChildren; label?: string }) {
  return (
    <div class="i-table-wrap">
      <table class="i-table" aria-label={label}>
        {children}
      </table>
    </div>
  );
}

/** A header (`th`) or data (`td`) cell; `num` right-aligns numbers in mono. */
export function Cell({
  as: Tag = "td",
  num,
  children,
  scope,
}: {
  as?: "td" | "th";
  num?: boolean;
  scope?: "col" | "row";
  children?: ComponentChildren;
}) {
  return (
    <Tag class={num ? "num" : undefined} scope={scope}>
      {children}
    </Tag>
  );
}
