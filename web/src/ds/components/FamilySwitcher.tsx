import { useEffect, useRef, useState } from "preact/hooks";
import type { FamilyApp } from "../../types";

const GRID = (
  <svg
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="square"
    aria-hidden="true"
  >
    <rect x="4" y="4" width="6.5" height="6.5" />
    <rect x="13.5" y="4" width="6.5" height="6.5" />
    <rect x="4" y="13.5" width="6.5" height="6.5" />
    <rect x="13.5" y="13.5" width="6.5" height="6.5" />
  </svg>
);

/**
 * The Instrument family switcher: ▦ before the wordmark, opening a menu of the family's apps.
 * Every link stays on this server's channel (`channel`, "live" or "lab"); an app with no copy
 * there is listed, disabled, with the reason. Renders nothing without apps. Below 600px the
 * menu opens full width.
 */
export function FamilySwitcher({ apps, channel }: { apps: FamilyApp[]; channel: string | null }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLElement>('a[role="menuitem"]')?.focus();
    const away = (e: Event) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);

  if (apps.length === 0) return null;
  const where = (channel ?? "").toUpperCase();

  const close = () => {
    setOpen(false);
    button.current?.focus();
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
      return;
    }
    const dir = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
    if (!dir) return;
    e.preventDefault();
    const items = [...(root.current?.querySelectorAll<HTMLElement>('a[role="menuitem"]') ?? [])];
    const at = items.indexOf(document.activeElement as HTMLElement);
    items[(at + dir + items.length) % items.length]?.focus();
  };

  return (
    <span class="d-switcher" ref={root}>
      <button
        type="button"
        class="i-switcher"
        ref={button}
        aria-label="Switch app"
        aria-haspopup="true"
        aria-expanded={open ? "true" : "false"}
        onClick={() => setOpen(!open)}
      >
        {GRID}
      </button>
      {open && (
        <div class="i-popover d-switcher-pop" role="menu" aria-label="Your apps" onKeyDown={onKey}>
          <div class="d-switcher-head">
            <span class="t-label">Your apps</span>
            {where && <span class="t-meta t-2">Opening in {where}</span>}
          </div>
          {apps.map((a) =>
            a.available && a.url ? (
              <a
                class="d-switcher-item"
                role="menuitem"
                href={a.url}
                aria-current={a.current ? "page" : undefined}
              >
                <span class="d-switcher-mark" aria-hidden="true">
                  {a.current ? "✓" : ""}
                </span>
                <span>{a.name}</span>
              </a>
            ) : (
              <span class="d-switcher-item d-switcher-item--off" role="menuitem" aria-disabled="true">
                <span class="d-switcher-mark" aria-hidden="true" />
                <span>{a.name}</span>
              </span>
            ),
          )}
          {apps.filter((a) => !a.available).map((a) => (
            <div class="d-switcher-foot" key={a.id}>
              <span class="i-reason">
                ⚑ {a.name} has no {where || "copy on this"} copy.
              </span>
            </div>
          ))}
          <div class="d-switcher-foot">
            <span class="t-meta t-2">Esc closes. ↑ ↓ moves. Enter opens.</span>
          </div>
        </div>
      )}
    </span>
  );
}
