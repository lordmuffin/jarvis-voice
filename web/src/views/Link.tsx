import type { ComponentChildren } from "preact";
import { navigate } from "../store";

/** An in-app link: a real `href` (new tab, copy link work) that navigates without a reload. */
export function Link({
  href,
  children,
  ...rest
}: {
  href: string;
  children: ComponentChildren;
  class?: string;
  "aria-current"?: "page";
  "data-testid"?: string;
}) {
  return (
    <a
      {...rest}
      href={href}
      onClick={(e) => {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        navigate(href);
      }}
    >
      {children}
    </a>
  );
}
