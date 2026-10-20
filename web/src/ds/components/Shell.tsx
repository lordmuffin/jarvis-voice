import type { ComponentChildren } from "preact";

/**
 * The app frame: header (brand, tabs, who you are), an optional banner under it, the page, then
 * the footer every family app shares (the family line, then `footer`, the version). Put
 * `class="inst"` here so every Instrument rule applies below it.
 */
export function Shell({
  brand,
  nav,
  bottom,
  tools,
  banner,
  footer,
  children,
}: {
  brand: ComponentChildren;
  nav: ComponentChildren;
  /** The same tabs for the narrow bottom bar (below 600px, where the header tabs hide). */
  bottom?: ComponentChildren;
  tools: ComponentChildren;
  banner?: ComponentChildren;
  /** The right side of the footer: the app, its version and commit. */
  footer?: ComponentChildren;
  children: ComponentChildren;
}) {
  return (
    <div class="inst d-app">
      <header class="i-header">
        <div class="i-header-in">
          <div class="i-brand">{brand}</div>
          <nav class={bottom ? "i-nav i-nav--wide" : "i-nav"} aria-label="Main">
            {nav}
          </nav>
          <div class="i-user">{tools}</div>
        </div>
      </header>
      {banner}
      <main class="i-page">{children}</main>
      <footer class="i-footer">
        <span>Private. Self-hosted. Open source.</span>
        {footer}
      </footer>
      {bottom && (
        <nav class="i-bottombar d-bottombar" aria-label="Main">
          {bottom}
        </nav>
      )}
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

/** The icon above a bottom-bar tab's label. */
export function TabIcon({ children }: { children: ComponentChildren }) {
  return (
    <span class="i-ico" aria-hidden="true">
      {children}
    </span>
  );
}
