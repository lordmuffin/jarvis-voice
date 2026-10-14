import { Segmented } from "../ds/components";
import type { Filter } from "../feed";
import { filterCounts, type Insight } from "../feed";

/** All / Actions / Decisions / Notes / Related / Work, each with how many there are. */
export function FeedFilter({
  insights,
  value,
  onChange,
}: {
  insights: Insight[];
  value: Filter;
  onChange: (f: Filter) => void;
}) {
  const c = filterCounts(insights);
  return (
    <Segmented<Filter>
      label="Show"
      value={value}
      onChange={onChange}
      options={[
        { id: "all", label: "All" },
        { id: "action", label: "Actions", count: c.action },
        { id: "decision", label: "Decisions", count: c.decision },
        { id: "note", label: "Notes", count: c.note },
        { id: "related", label: "Related", count: c.related },
        { id: "work", label: "Work", count: c.work },
      ]}
    />
  );
}
