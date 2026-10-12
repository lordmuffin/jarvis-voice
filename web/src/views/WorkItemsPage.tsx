import { useEffect, useState } from "preact/hooks";
import { ApiError, myWorkItems } from "../api";
import { currentUser } from "../store";
import type { WorkItemPage, WorkItemState } from "../types";
import { WorkItemList } from "./WorkItems";

const VIEWS: { id: string; label: string; states: WorkItemState[] }[] = [
  { id: "open", label: "Open", states: ["draft", "approved"] },
  { id: "discarded", label: "Discarded", states: ["discarded"] },
];

/** Every session's work items in one queue: review, approve, copy. */
export function WorkItemsPage() {
  const allowed = currentUser()?.work_items ?? false;
  const [view, setView] = useState(VIEWS[0]!);
  const [page, setPage] = useState<WorkItemPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setPage(await myWorkItems(view.states));
      setError(null);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError("Couldn't load work items.");
    }
  }

  useEffect(() => {
    if (allowed) void load();
  }, [view.id]);

  if (!allowed)
    return (
      <section class="page">
        <h1 class="h1">Work Items</h1>
        <p class="muted">Work items are found in the owner's and admins' sessions only.</p>
      </section>
    );

  return (
    <section class="page">
      <h1 class="h1">Work Items</h1>
      <p class="muted">
        Ideas from your sessions, as Claude Code prompts. Edit and approve one to freeze its
        prompt, then copy it into Claude Code.
      </p>
      <div class="toolbar" role="tablist">
        {VIEWS.map((v) => (
          <button
            key={v.id}
            role="tab"
            aria-selected={v.id === view.id}
            class={`btn btn-sm${v.id === view.id ? " btn-accent" : ""}`}
            onClick={() => setView(v)}
          >
            {v.label}
          </button>
        ))}
      </div>
      {error && (
        <p class="error" role="alert">
          {error}
        </p>
      )}
      {page === null ? (
        <p class="muted">Loading…</p>
      ) : page.items.length === 0 ? (
        <p class="muted">{view.id === "open" ? "Nothing to review." : "Nothing discarded."}</p>
      ) : (
        <WorkItemList page={page} onChanged={() => void load()} showSession />
      )}
    </section>
  );
}
