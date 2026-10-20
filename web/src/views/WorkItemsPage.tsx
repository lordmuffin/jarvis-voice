import { useState } from "preact/hooks";
import { ApiError, myWorkItems } from "../api";
import { Banner, EmptyState, Loading, PageTitle, Section, Segmented, Text } from "../ds/components";
import { currentUser } from "../store";
import type { WorkItemPage, WorkItemState } from "../types";
import { runActive } from "../workitems";
import { WorkItemList, usePolling } from "./WorkItems";

const VIEWS: { id: string; label: string; states: WorkItemState[] }[] = [
  { id: "open", label: "Open", states: ["draft", "approved"] },
  { id: "discarded", label: "Discarded", states: ["discarded"] },
  { id: "merged", label: "Merged", states: ["merged"] },
];

/** Every session's work items in one queue: review, approve, copy. */
export function WorkItemsPage() {
  const allowed = currentUser()?.work_items ?? false;
  const [view, setView] = useState(VIEWS[0]!);
  const [page, setPage] = useState<WorkItemPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [reloads, setReloads] = useState(0);

  async function load(): Promise<WorkItemPage | null> {
    if (!allowed) return null;
    try {
      const next = await myWorkItems(view.states);
      setPage(next);
      setError(null);
      return next;
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError("Couldn't load work items.");
      return null;
    }
  }

  // Keep a running item's status fresh until its run ends.
  usePolling(load, (p) => p.items.some(runActive), [view.id, reloads]);

  if (!allowed)
    return (
      <Section>
        <PageTitle>Work Items</PageTitle>
        <EmptyState>Work items are found in the owner's and admins' sessions only.</EmptyState>
      </Section>
    );

  return (
    <Section>
      <PageTitle>Work Items</PageTitle>
      <Text as="p" tone="muted" prose>
        Ideas from your sessions, as Claude Code prompts. Edit and approve one to freeze its
        prompt, then copy it into Claude Code.
      </Text>
      <div>
        <Segmented
          role="tablist"
          label="Work item views"
          value={view.id}
          options={VIEWS}
          onChange={(id) => setView(VIEWS.find((v) => v.id === id)!)}
        />
      </div>
      {error && <Banner variant="error">{error}</Banner>}
      {page === null ? (
        <Loading />
      ) : page.items.length === 0 ? (
        <EmptyState>
          {view.id === "open" ? "Nothing to review." : view.id === "merged" ? "Nothing merged yet." : "Nothing discarded."}
        </EmptyState>
      ) : (
        <WorkItemList page={page} onChanged={() => setReloads((n) => n + 1)} showSession />
      )}
    </Section>
  );
}
