import type { ComponentChildren } from "preact";
import { useEffect, useId, useRef } from "preact/hooks";

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * A modal. Esc and a click on the scrim close it; Tab stays inside; focus returns to what had
 * it before. `actions` is the footer (Cancel, then the confirm button).
 */
export function Dialog({
  title,
  onClose,
  actions,
  children,
}: {
  title: ComponentChildren;
  onClose: () => void;
  actions: ComponentChildren;
  children?: ComponentChildren;
}) {
  const box = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    // Start on the safest action: the first footer button (Cancel).
    const first = box.current?.querySelector<HTMLElement>(".i-dialog-ft button");
    (first ?? box.current)?.focus();
    return () => before?.focus?.();
  }, []);

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== "Tab" || !box.current) return;
    const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (items.length === 0) return;
    const firstEl = items[0]!;
    const lastEl = items[items.length - 1]!;
    if (e.shiftKey && document.activeElement === firstEl) {
      e.preventDefault();
      lastEl.focus();
    } else if (!e.shiftKey && document.activeElement === lastEl) {
      e.preventDefault();
      firstEl.focus();
    }
  }

  return (
    <div
      class="d-scrim"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        class="i-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        ref={box}
        onKeyDown={onKeyDown}
      >
        <div class="i-dialog-hd">
          <h2 id={titleId}>{title}</h2>
        </div>
        {children && <div class="i-dialog-bd">{children}</div>}
        <div class="i-dialog-ft">{actions}</div>
      </div>
    </div>
  );
}
