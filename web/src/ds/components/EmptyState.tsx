import type { ComponentChildren } from "preact";
import { Text } from "./Text";

/** Nothing to show, and what to do about it. */
export function EmptyState({
  children,
  hint,
  action,
}: {
  children: ComponentChildren;
  hint?: ComponentChildren;
  /** What to do about it (a Clear button). */
  action?: ComponentChildren;
}) {
  return (
    <div class="i-block">
      <Text as="p" tone="muted">
        {children}
      </Text>
      {hint && (
        <Text as="p" tone="muted" size="caption">
          {hint}
        </Text>
      )}
      {action}
    </div>
  );
}
