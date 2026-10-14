import type { ComponentChildren } from "preact";
import { Bar, Block, Button, Field, SearchInput, Segmented, Select } from "../ds/components";
import {
  isFiltered,
  type DateFilter,
  type Filters,
  type ModeFilter,
  type SearchIn,
  type SortKey,
  type StatusFilter,
} from "../sessionFilters";
import type { SessionStatus } from "../types";

const DATES: { id: DateFilter; label: string }[] = [
  { id: "any", label: "Any time" },
  { id: "today", label: "Today" },
  { id: "7d", label: "Last 7 days" },
  { id: "30d", label: "Last 30 days" },
];
const MODES: { id: ModeFilter; label: string }[] = [
  { id: "any", label: "Any mode" },
  { id: "meeting", label: "Meeting" },
  { id: "solo", label: "Solo" },
];
const SORTS: { id: SortKey; label: string }[] = [
  { id: "newest", label: "Newest first" },
  { id: "oldest", label: "Oldest first" },
  { id: "longest", label: "Longest first" },
];

/**
 * The Sessions toolbar: search (titles or transcripts) with whatever else belongs beside it
 * (`children`: All users, Upload), then status, mode, date and sort. Counts are per status under
 * the other filters; a count shows only where there is something to look at.
 */
export function SessionFilters({
  filters,
  counts,
  onChange,
  children,
}: {
  filters: Filters;
  counts: Record<SessionStatus, number> | null;
  onChange: (next: Filters) => void;
  children?: ComponentChildren;
}) {
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  const n = (s: SessionStatus) => (counts && s !== "done" && counts[s] > 0 ? counts[s] : undefined);
  return (
    <>
      <Block>
        <Bar>
          <SearchInput
            placeholder={filters.searchIn === "transcripts" ? "Search transcripts" : "Search by title"}
            aria-label={
              filters.searchIn === "transcripts" ? "Search sessions by transcript" : "Search sessions by title"
            }
            value={filters.q}
            onInput={(e) => set({ q: (e.target as HTMLInputElement).value })}
          />
          <Segmented<SearchIn>
            label="Search in"
            value={filters.searchIn}
            onChange={(searchIn) => set({ searchIn })}
            options={[
              { id: "titles", label: "Titles" },
              { id: "transcripts", label: "Transcripts" },
            ]}
          />
          {children}
        </Bar>
      </Block>
      <Block>
        <Bar>
          <Segmented<StatusFilter>
            label="Status"
            value={filters.status}
            onChange={(status) => set({ status })}
            options={[
              { id: "all", label: "All" },
              { id: "live", label: "Live", count: n("live") },
              { id: "finalizing", label: "Finalizing", count: n("finalizing") },
              { id: "done", label: "Done" },
              { id: "failed", label: "Failed", count: n("failed") },
            ]}
          />
          <Field label="Mode" htmlFor="filter-mode">
            <Select
              id="filter-mode"
              value={filters.mode}
              onChange={(e) => set({ mode: (e.target as HTMLSelectElement).value as ModeFilter })}
            >
              {MODES.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Date" htmlFor="filter-date">
            <Select
              id="filter-date"
              value={filters.date}
              onChange={(e) => set({ date: (e.target as HTMLSelectElement).value as DateFilter })}
            >
              {DATES.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Sort" htmlFor="filter-sort">
            <Select
              id="filter-sort"
              value={filters.sort}
              onChange={(e) => set({ sort: (e.target as HTMLSelectElement).value as SortKey })}
            >
              {SORTS.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </Select>
          </Field>
          {isFiltered(filters) && (
            <Button
              variant="quiet"
              size="sm"
              onClick={() => onChange({ ...filters, status: "all", mode: "any", date: "any", q: "" })}
            >
              Reset Filters
            </Button>
          )}
        </Bar>
      </Block>
    </>
  );
}
