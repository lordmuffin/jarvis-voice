import { useEffect, useState } from "preact/hooks";
import { ApiError, mergeWorkItems, sessionWorkItems } from "../api";
import { Banner, Bar, Block, Button, ListHead, Section, SectionLabel, Stack, Text } from "../ds/components";
import { when } from "../format";
import { WorkItemCard } from "../jarvis/WorkItemCard";
import { currentUser } from "../store";
import type { WorkItem, WorkItemPage } from "../types";
import { runActive, sortItems } from "../workitems";

export { WorkItemCard };

/** How often work items are refetched while a session is live or a run is going. */
export const POLL = { ms: 5000 };

const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

/**
 * The work items found in one session. Polls while the session is live (work items are not on
 * the WebSocket), and renders nothing for anyone but the session's owner or when the feature
 * is off.
 */
/** Refetch with `load` every POLL.ms while `keepGoing` holds for the latest result. */
export function usePolling<T>(load: () => Promise<T | null>, keepGoing: (r: T) => boolean, deps: unknown[]) {
  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      const r = await load();
      if (!stopped && r !== null && keepGoing(r)) timer = window.setTimeout(() => void tick(), POLL.ms);
    };
    void tick();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, deps);
}

export function SessionWorkItems({
  sessionId,
  ownerId,
  live,
  onPage,
}: {
  sessionId: string;
  ownerId: string;
  live: boolean;
  /** Told about each fetched page (the live feed lists work items among the findings). */
  onPage?: (page: WorkItemPage) => void;
}) {
  const user = currentUser();
  const mine = !!user?.work_items && user.id === ownerId;
  const [page, setPage] = useState<WorkItemPage | null>(null);
  const [reloads, setReloads] = useState(0);

  async function load(): Promise<WorkItemPage | null> {
    if (!mine) return null;
    try {
      const next = await sessionWorkItems(sessionId);
      setPage(next);
      onPage?.(next);
      return next;
    } catch {
      return page; /* 404 or offline: keep what we had, try again on the next poll */
    }
  }

  // While the session is live, or one of its items has a run going.
  usePolling(load, (p) => live || p.items.some(runActive), [sessionId, live, mine, reloads]);

  if (!mine || !page?.enabled) return null;
  if (!live && page.items.length === 0) return null;
  return (
    <Section aria-label="Work items" data-testid="work-items">
      <SectionLabel as="h3">Work items</SectionLabel>
      {page.items.length === 0 ? (
        <Text as="p" size="caption" tone="muted">
          Ideas you talk through become draft Claude Code prompts here.
        </Text>
      ) : (
        <WorkItemList page={page} onChanged={() => setReloads((n) => n + 1)} />
      )}
    </Section>
  );
}

/** Cards for a set of work items, with merging across drafts of one session. */
export function WorkItemList({
  page,
  onChanged,
  showSession = false,
  groupBySession = false,
  allItems,
}: {
  page: WorkItemPage;
  onChanged: () => void;
  showSession?: boolean;
  /** One heading per session (title and date), newest session first, as the server sends them. */
  groupBySession?: boolean;
  /** Every item, when `page` shows only some of them (to name what a merged item went into). */
  allItems?: WorkItem[];
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const items = sortItems(page.items);
  const pickedItems = items.filter((i) => picked.includes(i.id));
  const sameSession = new Set(pickedItems.map((i) => i.session_id)).size === 1;

  function toggle(id: string, on: boolean) {
    setPicked(on ? [...picked, id] : picked.filter((p) => p !== id));
  }

  async function merge(into: string) {
    setError(null);
    try {
      await mergeWorkItems(
        into,
        picked.filter((p) => p !== into),
      );
      setPicked([]);
      onChanged();
    } catch (e) {
      setError(errorText(e, "Couldn't merge those work items."));
    }
  }

  const titleOf = new Map((allItems ?? page.items).map((i) => [i.id, i.title]));
  const card = (item: WorkItem) => (
    <WorkItemCard
      key={item.id}
      item={item}
      repos={page.repos}
      dispatchEnabled={page.dispatch_enabled}
      showSession={showSession}
      sessionLine={!groupBySession}
      picked={picked.includes(item.id)}
      onPick={item.state === "draft" ? (on) => toggle(item.id, on) : undefined}
      onChanged={onChanged}
      mergedInto={
        item.state === "merged"
          ? ((item.merged_into && titleOf.get(item.merged_into)) || null)
          : undefined
      }
    />
  );
  const sessions: { id: string; title: string | null | undefined; started: string | null | undefined; items: WorkItem[] }[] = [];
  if (groupBySession)
    for (const i of page.items) {
      let g = sessions.find((x) => x.id === i.session_id);
      if (!g) sessions.push((g = { id: i.session_id, title: i.session_title, started: i.session_started_at, items: [] }));
      g.items.push(i);
    }

  return (
    <Stack>
      {pickedItems.length >= 2 && (
        <Block data-testid="merge-bar">
          <Bar>
            {sameSession ? (
              <>
                <Text size="caption">Merge {pickedItems.length} drafts into</Text>
                {pickedItems.map((i) => (
                  <Button key={i.id} size="sm" onClick={() => void merge(i.id)}>
                    {i.title}
                  </Button>
                ))}
              </>
            ) : (
              <Text size="caption" tone="muted">
                Only drafts from the same session can be merged.
              </Text>
            )}
            <Button variant="quiet" size="sm" onClick={() => setPicked([])}>
              Cancel
            </Button>
          </Bar>
        </Block>
      )}
      {error && <Banner variant="error">{error}</Banner>}
      {groupBySession
        ? sessions.map((g) => (
            <Stack key={g.id}>
              <ListHead title={g.title ?? "Untitled session"} meta={g.started ? when(g.started) : undefined} />
              {sortItems(g.items).map(card)}
            </Stack>
          ))
        : items.map(card)}
    </Stack>
  );
}

