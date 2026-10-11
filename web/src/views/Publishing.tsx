import { useEffect, useRef, useState } from "preact/hooks";
import {
  ApiError,
  getPublishing,
  publicationAction,
  publishAll,
  setAutoPublish,
  syncPublishing,
  type PublishAction,
} from "../api";
import { when } from "../format";
import { currentUser, me } from "../store";
import type { PublishingItem, PublishingQueue } from "../types";
import { Link } from "./Link";
import { PublicationChip } from "./PublicationChip";

const POLL_MS = 2000;
const SHOWN = 20; // rows per list until "Show all"

/**
 * Notes on their way to the vault repo: ready to publish, pull requests waiting to merge, and
 * what happened lately. The owner and admins only.
 */
export function Publishing() {
  const [q, setQ] = useState<PublishingQueue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // a session id, or "sync" / "all"
  const allowed = currentUser()?.can_publish ?? false;
  const poll = useRef<number | undefined>(undefined);

  async function load(fetcher: () => Promise<PublishingQueue> = getPublishing) {
    try {
      const next = await fetcher();
      setQ(next);
      setError(null);
      window.clearTimeout(poll.current);
      if (next.publishing_all) poll.current = window.setTimeout(() => void load(), POLL_MS);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError(e instanceof ApiError ? e.message : "Couldn't load the publishing queue.");
    }
  }

  useEffect(() => {
    if (allowed) void load();
    return () => window.clearTimeout(poll.current);
  }, []);

  async function act(item: PublishingItem, action: PublishAction) {
    if (action === "merge" && !confirm(`Merge “${item.title}” into the vault's main branch?`)) return;
    setBusy(item.session_id);
    try {
      await publicationAction(item.session_id, action);
      await load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError(`“${item.title}”: ${e instanceof ApiError ? e.message : "that didn't work"}`);
    } finally {
      setBusy(null);
    }
  }

  async function sync() {
    setBusy("sync");
    await load(syncPublishing);
    setBusy(null);
  }

  async function all() {
    if (!q || !confirm(`Open a pull request for each of the ${q.ready.length} ready notes?`)) return;
    setBusy("all");
    try {
      await publishAll();
      await load();
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401))
        setError(e instanceof ApiError ? e.message : "Couldn't start publishing.");
    } finally {
      setBusy(null);
    }
  }

  async function toggleAuto(on: boolean) {
    try {
      const updated = await setAutoPublish(on);
      me.value = updated;
      if (q) setQ({ ...q, auto_publish: updated.auto_publish });
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError("Couldn't change auto-publish.");
    }
  }

  if (!allowed)
    return (
      <section class="page">
        <h1 class="h1">Publishing</h1>
        <p class="muted">Only the owner and admins publish notes.</p>
      </section>
    );

  return (
    <section class="page">
      <h1 class="h1">Publishing</h1>
      <p class="muted">
        Each note goes to {q ? <code>{q.repo}</code> : "the vault repo"} as its own pull request
        against <code>{q?.base_branch ?? "main"}</code>. Review and merge here or on GitHub.
      </p>
      <div class="toolbar">
        <label class="toggle">
          <input
            type="checkbox"
            checked={q?.auto_publish ?? false}
            disabled={!q}
            onChange={(e) => void toggleAuto((e.target as HTMLInputElement).checked)}
          />
          Auto-publish new notes
        </label>
        <button class="btn btn-sm" disabled={busy !== null} onClick={() => void sync()}>
          {busy === "sync" ? "Syncing…" : "Sync Now"}
        </button>
      </div>
      {error && (
        <p class="error" role="alert">
          {error}
        </p>
      )}
      {!q && !error && <p class="muted">Loading…</p>}
      {q && (
        <>
          <div class="section-head">
            <h2 class="label">Ready to publish ({q.ready.length})</h2>
            {q.ready.length > 0 && (
              <button
                class="btn btn-sm btn-accent"
                disabled={busy !== null || q.publishing_all}
                onClick={() => void all()}
              >
                {q.publishing_all ? "Publishing…" : "Publish All"}
              </button>
            )}
          </div>
          <Items
            items={q.ready}
            empty="Nothing waiting. New notes show up here when a session ends."
            testid="ready"
            render={(it) => (
              <button
                class="btn btn-sm btn-accent"
                disabled={busy !== null || q.publishing_all}
                onClick={() => void act(it, "publish")}
              >
                Publish
              </button>
            )}
            busy={busy}
          />

          <h2 class="label section-head">Pull requests waiting to merge ({q.open.length})</h2>
          <Items
            items={q.open}
            empty="No open pull requests."
            testid="open"
            busy={busy}
            render={(it) => (
              <>
                {it.publication?.pr_url && (
                  <a
                    class="btn btn-sm btn-link"
                    href={`${it.publication.pr_url}/files`}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Review
                  </a>
                )}
                {it.publication?.state === "open" && (
                  <>
                    <button
                      class="btn btn-sm btn-accent"
                      disabled={busy !== null}
                      onClick={() => void act(it, "merge")}
                    >
                      Merge
                    </button>
                    <button class="btn btn-sm" disabled={busy !== null} onClick={() => void act(it, "close")}>
                      Close
                    </button>
                  </>
                )}
              </>
            )}
          />

          <h2 class="label section-head">Recent</h2>
          <Items
            items={q.recent}
            empty="Nothing yet."
            testid="recent"
            busy={busy}
            render={(it) =>
              it.publication?.state === "failed" ? (
                <button class="btn btn-sm" disabled={busy !== null} onClick={() => void act(it, "publish")}>
                  Retry
                </button>
              ) : it.publication?.state === "skipped" ? (
                <button class="btn btn-sm btn-quiet" disabled={busy !== null} onClick={() => void act(it, "unskip")}>
                  Unskip
                </button>
              ) : null
            }
          />
        </>
      )}
    </section>
  );
}

function Items({
  items,
  empty,
  testid,
  busy,
  render,
}: {
  items: PublishingItem[];
  empty: string;
  testid: string;
  busy: string | null;
  render: (it: PublishingItem) => preact.ComponentChildren;
}) {
  const [all, setAll] = useState(false);
  if (items.length === 0) return <p class="muted small">{empty}</p>;
  const shown = all ? items : items.slice(0, SHOWN);
  return (
    <>
      <ul class="rows" data-testid={`publishing-${testid}`}>
        {shown.map((it) => (
          <li class="row row-static" key={it.session_id} data-testid="publishing-item">
            <div class="row-main">
              <Link class="row-title" href={`/sessions/${encodeURIComponent(it.session_id)}`}>
                {it.title}
              </Link>
              <span class="meta">
                {when(it.started_at)} · {it.owner}
                {it.publication?.pr_url && (
                  <>
                    {" · "}
                    <a href={it.publication.pr_url} target="_blank" rel="noopener noreferrer">
                      PR #{it.publication.pr_number}
                    </a>
                  </>
                )}
              </span>
              {it.publication?.state === "failed" && it.publication.last_error && (
                <span class="error small">{it.publication.last_error}</span>
              )}
            </div>
            <div class="row-actions">
              {it.publication && <PublicationChip publication={it.publication} />}
              {busy === it.session_id ? <span class="meta">Working…</span> : render(it)}
            </div>
          </li>
        ))}
      </ul>
      {shown.length < items.length && (
        <button class="btn btn-sm btn-quiet" onClick={() => setAll(true)}>
          Show all {items.length}
        </button>
      )}
    </>
  );
}
