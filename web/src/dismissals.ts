// Dismissed copilot suggestions, remembered per session in this browser. The server has no
// endpoint for this yet, so a dismissal does not follow you to another device. Storage can throw
// (private window, blocked site data); then a dismissal lasts until the page reloads.

const KEY = (sessionId: string) => `jarvis-live:dismissed:${sessionId}`;
const MAX = 200;

function read(sessionId: string): string[] {
  try {
    const raw = localStorage.getItem(KEY(sessionId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function write(sessionId: string, ids: string[]): void {
  try {
    localStorage.setItem(KEY(sessionId), JSON.stringify(ids.slice(-MAX)));
  } catch {
    /* it just won't survive a reload */
  }
}

export function loadDismissed(sessionId: string): Set<string> {
  return new Set(read(sessionId));
}

export function dismissSuggestion(sessionId: string, id: string): void {
  const ids = read(sessionId).filter((x) => x !== id);
  write(sessionId, [...ids, id]);
}

export function restoreSuggestion(sessionId: string, id: string): void {
  write(
    sessionId,
    read(sessionId).filter((x) => x !== id),
  );
}
