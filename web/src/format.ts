const pad = (n: number) => String(n).padStart(2, "0");

/** `m:ss`, or `h:mm:ss` from one hour up. */
export function clock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export function when(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function duration(startIso: string, endIso: string | null): string {
  if (!endIso) return "";
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "";
  const min = Math.round(ms / 60000);
  return min < 1 ? "<1 min" : `${min} min`;
}

export const titleOf = (s: { title: string | null }) => s.title?.trim() || "Untitled session";

/** Case-insensitive title search; an empty query matches everything. */
export function matchesTitle(s: { title: string | null }, query: string): boolean {
  const q = query.trim().toLowerCase();
  return q === "" || titleOf(s).toLowerCase().includes(q);
}

/** A file size: `900 B`, `39.1 KB`, `36.4 MB`, `1.2 GB` (binary units). */
export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}
