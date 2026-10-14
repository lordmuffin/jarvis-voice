import type { ComponentChildren } from "preact";

/**
 * The app frame: header (brand, tabs, who you are), an optional banner under it, then the page.
 * Put `class="inst"` here so every Instrument rule applies below it.
 */
export function Shell({
  brand,
  nav,
  tools,
  banner,
  footer,
  children,
}: {
  brand: ComponentChildren;
  nav: ComponentChildren;
  tools: ComponentChildren;
  banner?: ComponentChildren;
  /** A quiet line at the bottom of the page. */
  footer?: ComponentChildren;
  children: ComponentChildren;
}) {
  return (
    <div class="inst d-app">
      <header class="i-header">
        <div class="i-header-in">
          <div class="i-brand">{brand}</div>
          <nav class="i-nav" aria-label="Main">
            {nav}
          </nav>
          <div class="i-user">{tools}</div>
        </div>
      </header>
      {banner}
      <main class="i-page">{children}</main>
      {footer && <footer class="i-footer">{footer}</footer>}
    </div>
  );
}

/** A full-height centered card for screens with no shell (sign-in, boot). */
export function CenterCard({ children }: { children: ComponentChildren }) {
  return (
    <div class="inst d-center">
      <div class="d-card">{children}</div>
    </div>
  );
}
