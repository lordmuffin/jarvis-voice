import type { ComponentChildren } from "preact";
import { Text } from "./Text";

/** Nothing to show, and what to do about it. */
export function EmptyState({ children, hint }: { children: ComponentChildren; hint?: ComponentChildren }) {
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
    </div>
  );
}
