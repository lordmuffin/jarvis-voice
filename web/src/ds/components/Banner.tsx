import type { ComponentChildren } from "preact";
import { cx } from "../classes";

export type BannerVariant = "info" | "success" | "warning" | "error" | "viewing";

/** A full-width notice: a bold lead, the message, and an optional action on the right. */
export function Banner({
  variant = "info",
  title,
  action,
  children,
  ...rest
}: {
  variant?: BannerVariant;
  title?: ComponentChildren;
  action?: ComponentChildren;
  children?: ComponentChildren;
  "data-testid"?: string;
}) {
  return (
    <div
      {...rest}
      class={cx("i-banner", variant !== "info" && `i-banner--${variant}`)}
      role={variant === "error" ? "alert" : "status"}
    >
      {title && <b>{title}</b>}
      {children}
      {action && <span class="i-banner-act">{action}</span>}
    </div>
  );
}
