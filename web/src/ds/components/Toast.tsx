import type { ComponentChildren } from "preact";

/** A short-lived confirmation. Put it inside a `ToastRegion`. */
export function Toast({ title, children }: { title?: ComponentChildren; children: ComponentChildren }) {
  return (
    <div class="i-toast" role="status">
      {title && <b>{title}</b>}
      {children}
    </div>
  );
}

export function ToastRegion({ children }: { children: ComponentChildren }) {
  return <div class="d-toasts">{children}</div>;
}
