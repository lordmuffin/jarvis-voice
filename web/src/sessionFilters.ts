import type { ListOptions } from "./api";
import type { SessionStatus } from "./types";

export type StatusFilter = "all" | SessionStatus;
export type ModeFilter = "any" | "meeting" | "solo";
export type DateFilter = "any" | "today" | "7d" | "30d";
export type SortKey = "newest" | "oldest" | "longest";
export type SearchIn = "titles" | "transcripts";

/** What the Sessions list is narrowed to. The URL holds it, so a view can be shared. */
export interface Filters {
  status: StatusFilter;
  mode: ModeFilter;
  date: DateFilter;
  sort: SortKey;
  q: string;
  searchIn: SearchIn;
}

export const DEFAULT_FILTERS: Filters = {
  status: "all",
  mode: "any",
  date: "any",
  sort: "newest",
  q: "",
  searchIn: "titles",
};

const STATUSES: readonly StatusFilter[] = ["all", "live", "finalizing", "done", "failed"];
const MODES: readonly ModeFilter[] = ["any", "meeting", "solo"];
const DATES: readonly DateFilter[] = ["any", "today", "7d", "30d"];
const SORTS: readonly SortKey[] = ["newest", "oldest", "longest"];
const SCOPES: readonly SearchIn[] = ["titles", "transcripts"];

const pick = <T extends string>(allowed: readonly T[], value: string | null, fallback: T): T =>
  allowed.includes(value as T) ? (value as T) : fallback;

/** The filters in a URL query; anything unknown falls back to the default. */
export function parseFilters(search: string): Filters {
  const p = new URLSearchParams(search);
  return {
    status: pick(STATUSES, p.get("status"), DEFAULT_FILTERS.status),
    mode: pick(MODES, p.get("mode"), DEFAULT_FILTERS.mode),
    date: pick(DATES, p.get("date"), DEFAULT_FILTERS.date),
    sort: pick(SORTS, p.get("sort"), DEFAULT_FILTERS.sort),
    q: p.get("q") ?? "",
    searchIn: pick(SCOPES, p.get("in"), DEFAULT_FILTERS.searchIn),
  };
}

const OURS = ["status", "mode", "date", "sort", "q", "in"];

/** `base` (the URL's other params, like `all` and `user`) plus whatever differs from the default. */
export function filtersToSearch(f: Filters, base: string): string {
  const p = new URLSearchParams(base);
  for (const k of OURS) p.delete(k);
  if (f.status !== DEFAULT_FILTERS.status) p.set("status", f.status);
  if (f.mode !== DEFAULT_FILTERS.mode) p.set("mode", f.mode);
  if (f.date !== DEFAULT_FILTERS.date) p.set("date", f.date);
  if (f.sort !== DEFAULT_FILTERS.sort) p.set("sort", f.sort);
  if (f.q.trim()) {
    p.set("q", f.q);
    if (f.searchIn !== DEFAULT_FILTERS.searchIn) p.set("in", f.searchIn);
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

const DAY_MS = 86_400_000;

/** The start of the window a date filter means, in the viewer's time zone (ISO, for the server). */
export function sinceFor(date: DateFilter, now: Date = new Date()): string | undefined {
  switch (date) {
    case "any":
      return undefined;
    case "today":
      return new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
    case "7d":
      return new Date(now.getTime() - 7 * DAY_MS).toISOString();
    case "30d":
      return new Date(now.getTime() - 30 * DAY_MS).toISOString();
  }
}

/** The server parameters for a set of filters (nothing for the defaults). */
export function toListOptions(f: Filters, now: Date = new Date()): ListOptions {
  const q = f.q.trim();
  const o: ListOptions = {};
  if (f.status !== "all") o.status = f.status;
  if (f.mode !== "any") o.mode = f.mode;
  const since = sinceFor(f.date, now);
  if (since) o.since = since;
  if (f.sort !== "newest") o.sort = f.sort;
  if (q) {
    o.q = q;
    o.searchIn = f.searchIn;
  }
  return o;
}

/** Is the list narrowed (sort alone does not narrow it)? */
export function isFiltered(f: Filters): boolean {
  return f.status !== "all" || f.mode !== "any" || f.date !== "any" || f.q.trim() !== "";
}

export const MATCH_START = "";
export const MATCH_STOP = "";

/** A search snippet as text parts, split at the server's match markers. Never HTML. */
export function snippetParts(snippet: string): { text: string; hit: boolean }[] {
  const parts: { text: string; hit: boolean }[] = [];
  let rest = snippet;
  while (rest.length > 0) {
    const start = rest.indexOf(MATCH_START);
    if (start < 0) {
      parts.push({ text: rest, hit: false });
      break;
    }
    if (start > 0) parts.push({ text: rest.slice(0, start), hit: false });
    const stop = rest.indexOf(MATCH_STOP, start + 1);
    const end = stop < 0 ? rest.length : stop;
    parts.push({ text: rest.slice(start + 1, end), hit: true });
    rest = stop < 0 ? "" : rest.slice(stop + 1);
  }
  return parts;
}
