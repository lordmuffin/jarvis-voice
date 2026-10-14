import { useEffect, useMemo, useState } from "preact/hooks";
import { EmptyState, ListHead } from "../ds/components";
import {
  buildFeed,
  groupInsights,
  markNew,
  type Arrivals,
  type Filter,
  type Insight,
} from "../feed";
import { clock } from "../format";
import { useFollow } from "../follow";
import type { Look } from "../look";
import type { Line } from "../reducer";
import type { FinalNoteRef, Marker } from "../types";
import { hashTime } from "../workitems";
import { CopilotItem, FinalNoteRow } from "./CopilotItem";
import { JumpPill } from "./JumpPill";
import { MarkerRow } from "./MarkerRow";
import { RelatedNoteCard } from "./RelatedNoteCard";
import { TranscriptLine } from "./TranscriptLine";

/**
 * The transcript, and (live) the copilot's findings among it. The `role="log"` element is the
 * scroller: it follows the bottom while you read the latest, pauses when you scroll up, and a
 * pill brings you back. The Now dock is not in here, so it never scrolls.
 */
export function Timeline({
  lines,
  markers,
  insights = [],
  arrivals = {},
  finalNote,
  finalAt,
  filter = "all",
  look = { streamMs: 0, wallMs: 0 },
  live = false,
  stack,
  label,
  toolbar,
}: {
  lines: Line[];
  markers: Marker[];
  insights?: Insight[];
  arrivals?: Arrivals;
  finalNote?: FinalNoteRef | null;
  finalAt?: number;
  filter?: Filter;
  /** When you last looked; findings that arrived after it are new. */
  look?: Look;
  /** Follow the latest line. Past sessions don't. */
  live?: boolean;
  stack?: boolean;
  label: string;
  /** The filter, shown above the log with the follow state beside it. */
  toolbar?: preact.ComponentChildren;
}) {
  // `#t=<ms>` (a work item's source link) highlights the line spoken then.
  const [target, setTarget] = useState<number | null>(() => hashTime(location.hash));
  useEffect(() => {
    const on = () => setTarget(hashTime(location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const feed = useMemo(
    () => buildFeed(lines, markers, filter === "all" ? insights : [], arrivals),
    [lines, markers, insights, arrivals, filter],
  );
  const marks = useMemo(() => markNew(feed, arrivals, look.wallMs), [feed, arrivals, look.wallMs]);
  const grouped = filter !== "all";
  const entryCount = feed.length + (finalNote ? 1 : 0);
  const follow = useFollow(entryCount, live && !grouped && target === null);

  // The line containing the target time, else the last one starting before it.
  const hit =
    target === null
      ? undefined
      : (lines.find((l) => l.start_ms <= target && target < l.end_ms) ??
        [...lines].reverse().find((l) => l.start_ms <= target));
  useEffect(() => {
    if (!hit) return;
    document.querySelector(`[data-line="${CSS.escape(hit.id)}"]`)?.scrollIntoView({ block: "center" });
  }, [hit?.id, target]);

  const empty = grouped ? groupInsights(insights, filter).length === 0 : feed.length === 0 && !finalNote;
  const role = live ? "log" : "group";

  return (
    <div class="d-feedwrap">
      {toolbar && (
        <div class="d-bar">
          {toolbar}
          {live && !grouped && (
            <span class={follow.following ? "t-meta d-end d-tone-accent" : "t-meta d-end d-tone-muted"} data-testid="follow-state">
              {follow.following ? "↓ Following live" : "Follow paused"}
            </span>
          )}
        </div>
      )}
      <div
        ref={follow.setEl}
        class={`d-log${live ? "" : " d-log--past"}${empty ? " d-log--empty" : ""}`}
        role={role}
        aria-live={live ? "polite" : undefined}
        aria-label={label}
        tabIndex={live ? undefined : 0}
        onScroll={follow.onScroll}
      >
        {empty ? (
          <EmptyState>Nothing yet.</EmptyState>
        ) : grouped ? (
          groupInsights(insights, filter).map((g) => (
            <div key={g.kind} class="i-stack">
              <ListHead title={GROUP[g.kind]} count={g.items.length} />
              {g.items.map((i) =>
                i.kind === "related" ? (
                  <RelatedNoteCard
                    key={i.key}
                    related={{ path: i.meta ?? "", title: i.text, snippet: i.snippet ?? "", uri: i.href ?? "" }}
                  />
                ) : (
                  <CopilotItem key={i.key} insight={i} variant="item" />
                ),
              )}
            </div>
          ))
        ) : (
          <>
            {feed.map((e) => (
              <FeedRow key={e.key}>
                {marks.firstNewKey === e.key && (
                  <div class="i-unseen" role="separator" data-testid="new-rule">
                    <span class="i-new">
                      {marks.count} new since you looked at {clock(look.streamMs)}
                    </span>
                  </div>
                )}
                {e.kind === "line" ? (
                  <TranscriptLine line={e.line} target={e.line.id === hit?.id} stack={stack} />
                ) : e.kind === "marker" ? (
                  <MarkerRow marker={e.marker} />
                ) : (
                  <CopilotItem
                    insight={e.insight}
                    variant="event"
                    at={arrivals[e.key] ? Math.max(arrivals[e.key]!.at, e.insight.srcMs ?? 0) : undefined}
                    isNew={marks.newKeys.has(e.key)}
                  />
                )}
              </FeedRow>
            ))}
            {finalNote && <FinalNoteRow note={finalNote} at={finalAt} />}
          </>
        )}
      </div>
      {live && !follow.following && !grouped && <JumpPill count={follow.newCount} onJump={follow.jump} />}
    </div>
  );
}

const GROUP = {
  action: "Actions",
  decision: "Decisions",
  note: "Notes",
  related: "Related",
  work: "Work items",
} as const;

/** A fragment that keeps the feed's rows flat (the log is a flex column). */
function FeedRow({ children }: { children: preact.ComponentChildren }) {
  return <>{children}</>;
}
