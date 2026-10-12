import { useEffect, useRef, useState } from "preact/hooks";
import {
  ApiError,
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
import { STATE_LABEL, copyText, parseCriteria, sortItems, transcriptHref } from "../workitems";
import { Link } from "./Link";

const LIVE_POLL_MS = 5000;

const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

/**
 * The work items found in one session. Polls while the session is live (work items are not on
 * the WebSocket), and renders nothing for anyone but the session's owner or when the feature
 * is off.
 */
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
  const timer = useRef<number | undefined>(undefined);

  async function load() {
    try {
      setPage(await sessionWorkItems(sessionId));
    } catch {
      /* 404 or offline: show nothing, try again on the next poll */
    }
  }

  useEffect(() => {
    if (!mine) return;
    void load();
    if (live) timer.current = window.setInterval(() => void load(), LIVE_POLL_MS);
    return () => window.clearInterval(timer.current);
  }, [sessionId, live, mine]);

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
        <WorkItemList page={page} onChanged={() => void load()} />
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
          showSession={showSession}
          picked={picked.includes(item.id)}
          onPick={item.state === "draft" ? (on) => toggle(item.id, on) : undefined}
          onChanged={onChanged}
        />
      ))}
    </div>
  );
}

type Busy = WorkItemAction | "save" | "copy" | "prompt" | null;

export function WorkItemCard({
  item,
  repos,
  showSession,
  picked,
  onPick,
  onChanged,
}: {
  item: WorkItem;
  repos: WorkRepo[];
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

      {!editing && (
        <div class="wi-actions">
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
