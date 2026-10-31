import { signal } from "@preact/signals";

export type Theme = "system" | "light" | "dark";
export const THEMES: readonly Theme[] = ["system", "light", "dark"];
export const THEME_KEY = "jarvis-live:theme";

/** The viewer's choice. "system" follows prefers-color-scheme (no data-theme override). */
export const theme = signal<Theme>("system");

const isTheme = (v: unknown): v is Theme => THEMES.includes(v as Theme);

function apply(t: Theme): void {
  if (t === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
}

/** Read the saved choice and apply it. Storage can throw (private window, blocked data). */
export function applyStoredTheme(): void {
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(THEME_KEY);
  } catch {
    /* no storage: follow the system */
  }
  theme.value = isTheme(saved) ? saved : "system";
  apply(theme.value);
}

export function setTheme(t: Theme): void {
  theme.value = t;
  apply(t);
  try {
    if (t === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, t);
  } catch {
    /* the choice just won't survive a reload */
  }
}
