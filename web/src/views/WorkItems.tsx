import { useEffect, useState } from "preact/hooks";
import {
  ApiError,
  cancelDispatch,
  dispatchWorkItem,
  editWorkItem,
  mergeWorkItems,
  sessionWorkItems,
  workItemAction,
  workItemCopied,
  workItemPrompt,
  type WorkItemAction,
} from "../api";
import { clock } from "../format";
import { currentUser } from "../store";
import type { WorkItem, WorkItemEdit, WorkItemPage, WorkItemPrompt, WorkRepo } from "../types";
import {
  DISPATCH_LABEL,
  STATE_LABEL,
  copyText,
  parseCriteria,
  runActive,
  sortItems,
  transcriptHref,
} from "../workitems";
import { Link } from "./Link";

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
}: {
  sessionId: string;
  ownerId: string;
  live: boolean;
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
    <section class="work-items" aria-label="Work items" data-testid="work-items">
      <h3 class="label">Work items</h3>
      {page.items.length === 0 ? (
        <p class="muted small">
          Ideas you talk through become draft Claude Code prompts here.
        </p>
      ) : (
        <WorkItemList page={page} onChanged={() => setReloads((n) => n + 1)} />
      )}
    </section>
  );
}

/** Cards for a set of work items, with merging across drafts of one session. */
export function WorkItemList({
  page,
  onChanged,
  showSession = false,
}: {
  page: WorkItemPage;
  onChanged: () => void;
  showSession?: boolean;
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

  return (
    <div class="wi-list">
      {pickedItems.length >= 2 && (
        <div class="wi-mergebar" data-testid="merge-bar">
          {sameSession ? (
            <>
              <span class="small">Merge {pickedItems.length} drafts into</span>
              {pickedItems.map((i) => (
                <button key={i.id} class="btn btn-sm" onClick={() => void merge(i.id)}>
                  {i.title}
                </button>
              ))}
            </>
          ) : (
            <span class="small muted">Only drafts from the same session can be merged.</span>
          )}
          <button class="btn btn-sm btn-quiet" onClick={() => setPicked([])}>
            Cancel
          </button>
        </div>
      )}
      {error && (
        <p class="error small" role="alert">
          {error}
        </p>
      )}
      {items.map((item) => (
        <WorkItemCard
          key={item.id}
          item={item}
          repos={page.repos}
          dispatchEnabled={page.dispatch_enabled}
          showSession={showSession}
          picked={picked.includes(item.id)}
          onPick={item.state === "draft" ? (on) => toggle(item.id, on) : undefined}
          onChanged={onChanged}
        />
      ))}
    </div>
  );
}

type Busy = WorkItemAction | "save" | "copy" | "prompt" | "run" | "cancel" | null;

export function WorkItemCard({
  item,
  repos,
  dispatchEnabled = false,
  showSession,
  picked,
  onPick,
  onChanged,
}: {
  item: WorkItem;
  repos: WorkRepo[];
  dispatchEnabled?: boolean;
  showSession: boolean;
  picked: boolean;
  onPick?: (on: boolean) => void;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [prompt, setPrompt] = useState<WorkItemPrompt | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function run(kind: Busy, fn: () => Promise<unknown>, fallback: string) {
    setBusy(kind);
    setError(null);
    try {
      await fn();
      return true;
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError(errorText(e, fallback));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function act(action: WorkItemAction) {
    if (action === "discard" && !confirm(`Discard “${item.title}”?`)) return;
    if (await run(action, () => workItemAction(item.id, action), "That didn't work.")) {
      setPrompt(null);
      onChanged();
    }
  }

  async function runIt() {
    const repo = item.repo ?? "its repository";
    if (!confirm(`Run “${item.title}” as Claude Code on ${repo}? It opens a draft PR.`)) return;
    if (await run("run", () => dispatchWorkItem(item.id), "Couldn't start the run.")) onChanged();
  }

  async function cancelRun() {
    if (await run("cancel", () => cancelDispatch(item.id), "Couldn't cancel the run.")) onChanged();
  }

  async function togglePrompt() {
    if (prompt) return setPrompt(null);
    await run("prompt", async () => setPrompt(await workItemPrompt(item.id)), "Couldn't load the prompt.");
  }

  async function copy() {
    await run(
      "copy",
      async () => {
        const p = await workItemPrompt(item.id);
        if (!(await copyText(p.markdown))) throw new Error("copy failed");
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
        await workItemCopied(item.id);
      },
      "Couldn't copy the prompt.",
    );
  }

  const open = item.state === "draft" || item.state === "approved";
  return (
    <article class={`wi wi-${item.state}`} data-testid="work-item" data-state={item.state}>
      <header class="wi-head">
        {onPick && (
          <input
            type="checkbox"
            class="wi-pick"
            checked={picked}
            aria-label={`Select “${item.title}” to merge`}
            onChange={(e) => onPick((e.target as HTMLInputElement).checked)}
          />
        )}
        <div class="wi-title">
          <strong data-testid="work-item-title">{item.title}</strong>
          <div class="meta">
            <span class={`chip chip-wi-${item.state}`}>{STATE_LABEL[item.state]}</span>{" "}
            {item.repo ? <code>{item.repo}</code> : <span>no repository yet</span>}
            {showSession && (
              <>
                {" · "}
                <Link href={`/sessions/${encodeURIComponent(item.session_id)}`}>
                  {item.session_title ?? "Untitled session"}
                </Link>
              </>
            )}
          </div>
        </div>
      </header>

      {editing ? (
        <WorkItemEditor
          item={item}
          repos={repos}
          busy={busy === "save"}
          onCancel={() => setEditing(false)}
          onSave={async (fields) => {
            if (await run("save", () => editWorkItem(item.id, fields), "Couldn't save.")) {
              setEditing(false);
              setPrompt(null);
              onChanged();
            }
          }}
        />
      ) : (
        <>
          {item.intent && <p class="wi-intent">{item.intent}</p>}
          {item.acceptance_criteria.length > 0 && (
            <ul class="wi-criteria">
              {item.acceptance_criteria.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          )}
          {item.done_when && <p class="small muted">Done when: {item.done_when}</p>}
          {item.sources.length > 0 && (
            <div class="wi-sources" aria-label="Where it was said">
              {item.sources.map((s) =>
                showSession ? (
                  <Link
                    key={s.segment_id}
                    class="wi-source"
                    href={transcriptHref(item.session_id, s.start_ms)}
                    title={s.text}
                  >
                    ▶ {clock(s.start_ms)}
                  </Link>
                ) : (
                  // On the session's own page: a hash change scrolls its transcript there.
                  <a key={s.segment_id} class="wi-source" href={`#t=${s.start_ms}`} title={s.text}>
                    ▶ {clock(s.start_ms)}
                  </a>
                ),
              )}
            </div>
          )}
          {item.state === "draft" && item.problems.length > 0 && (
            <p class="small muted" data-testid="work-item-problems">
              To approve: {item.problems.join("; ")}.
            </p>
          )}
        </>
      )}

      {item.dispatch && <DispatchStatus run={item.dispatch} />}

      {!editing && (
        <div class="wi-actions">
          {dispatchEnabled && item.state === "approved" && !runActive(item) && (
            <button class="btn btn-sm btn-accent" disabled={busy !== null} onClick={() => void runIt()}>
              {busy === "run" ? "Starting…" : item.dispatch ? "▶ Run Again" : "▶ Run in Claude Code"}
            </button>
          )}
          {runActive(item) && (
            <button class="btn btn-sm" disabled={busy !== null} onClick={() => void cancelRun()}>
              {busy === "cancel" ? "Cancelling…" : "■ Cancel Run"}
            </button>
          )}
          {item.state === "approved" && (
            <button class="btn btn-sm btn-accent" disabled={busy !== null} onClick={() => void copy()}>
              {copied ? "✓ Copied" : busy === "copy" ? "Copying…" : "⎘ Copy Prompt"}
            </button>
          )}
          {item.state === "draft" && (
            <button
              class="btn btn-sm btn-accent"
              disabled={busy !== null || item.problems.length > 0}
              title={item.problems.length ? item.problems.join("; ") : undefined}
              onClick={() => void act("approve")}
            >
              {busy === "approve" ? "Approving…" : "✓ Approve"}
            </button>
          )}
          {open && (
            <>
              <button class="btn btn-sm" disabled={busy !== null} onClick={() => void togglePrompt()}>
                {prompt ? "Hide Prompt" : item.state === "approved" ? "Show Prompt" : "Preview Prompt"}
              </button>
              <button class="btn btn-sm" disabled={busy !== null} onClick={() => setEditing(true)}>
                Edit
              </button>
            </>
          )}
          {item.state === "approved" && (
            <button class="btn btn-sm btn-quiet" disabled={busy !== null} onClick={() => void act("unapprove")}>
              Unapprove
            </button>
          )}
          {open && (
            <button class="btn btn-sm btn-quiet" disabled={busy !== null} onClick={() => void act("discard")}>
              ✕ Discard
            </button>
          )}
          {item.state === "discarded" && (
            <button class="btn btn-sm" disabled={busy !== null} onClick={() => void act("restore")}>
              ↩ Restore
            </button>
          )}
        </div>
      )}

      {error && (
        <p class="error small" role="alert">
          {error}
        </p>
      )}
      {prompt && (
        <div class="wi-prompt">
          <p class="meta">
            {prompt.frozen
              ? `Approved prompt · sha256 ${prompt.sha256?.slice(0, 12)}`
              : "Preview. Approving freezes the prompt exactly as shown."}
          </p>
          <pre data-testid="work-item-prompt">{prompt.markdown}</pre>
        </div>
      )}
    </article>
  );
}

/** The latest run: where it stands, its PR, or why it ended. */
function DispatchStatus({ run }: { run: NonNullable<WorkItem["dispatch"]> }) {
  const bad = ["failed", "timed_out", "rate_limited"].includes(run.state);
  return (
    <div class={`wi-run wi-run-${run.state}`} data-testid="dispatch">
      <span class="meta">
        Claude Code{run.attempt > 1 ? ` · attempt ${run.attempt}` : ""} ·{" "}
        <strong>{DISPATCH_LABEL[run.state]}</strong>
        {run.pr_url && (
          <>
            {" · "}
            <a href={run.pr_url} target="_blank" rel="noreferrer">
              Draft PR #{run.pr_number}
            </a>
          </>
        )}
      </span>
      {run.summary && <p class="small">{run.summary}</p>}
      {bad && run.error && <p class="small error">{run.error}</p>}
    </div>
  );
}

function WorkItemEditor({
  item,
  repos,
  busy,
  onSave,
  onCancel,
}: {
  item: WorkItem;
  repos: WorkRepo[];
  busy: boolean;
  onSave: (fields: WorkItemEdit) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(item.title);
  const [repo, setRepo] = useState(item.repo ?? "");
  const [intent, setIntent] = useState(item.intent);
  const [criteria, setCriteria] = useState(item.acceptance_criteria.join("\n"));
  const [doneWhen, setDoneWhen] = useState(item.done_when ?? "");

  function save(e: Event) {
    e.preventDefault();
    const fields: WorkItemEdit = {};
    if (title.trim() !== item.title) fields.title = title;
    if ((repo || null) !== item.repo) fields.repo = repo || null;
    if (intent.trim() !== item.intent) fields.intent = intent;
    const parsed = parseCriteria(criteria);
    if (parsed.join("\n") !== item.acceptance_criteria.join("\n")) fields.acceptance_criteria = parsed;
    if ((doneWhen.trim() || null) !== item.done_when) fields.done_when = doneWhen.trim() || null;
    if (Object.keys(fields).length === 0) return onCancel();
    onSave(fields);
  }

  const id = (f: string) => `wi-${item.id}-${f}`;
  return (
    <form class="wi-form" onSubmit={save}>
      <label class="label" for={id("title")}>
        Title
      </label>
      <input id={id("title")} class="field" value={title} onInput={(e) => setTitle(e.currentTarget.value)} />
      <label class="label" for={id("repo")}>
        Repository
      </label>
      <select id={id("repo")} class="field" value={repo} onChange={(e) => setRepo(e.currentTarget.value)}>
        <option value="">Choose a repository…</option>
        {repos.map((r) => (
          <option key={r.name} value={r.name}>
            {r.name}
          </option>
        ))}
      </select>
      <label class="label" for={id("intent")}>
        Intent
      </label>
      <textarea
        id={id("intent")}
        class="field wi-textarea"
        rows={4}
        value={intent}
        onInput={(e) => setIntent(e.currentTarget.value)}
      />
      <label class="label" for={id("criteria")}>
        Acceptance criteria (one per line)
      </label>
      <textarea
        id={id("criteria")}
        class="field wi-textarea"
        rows={4}
        value={criteria}
        onInput={(e) => setCriteria(e.currentTarget.value)}
      />
      <label class="label" for={id("done")}>
        Done when
      </label>
      <input id={id("done")} class="field" value={doneWhen} onInput={(e) => setDoneWhen(e.currentTarget.value)} />
      <div class="wi-actions">
        <button class="btn btn-sm btn-accent" type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button class="btn btn-sm btn-quiet" type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
      {item.state === "approved" && (
        <p class="small muted">Saving puts it back to draft: approve it again to freeze the new prompt.</p>
      )}
    </form>
  );
}
