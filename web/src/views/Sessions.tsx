import { useEffect, useState } from "preact/hooks";
import { ApiError, listSessions, listUsers, type ListOptions } from "../api";
import { duration, matchesTitle, titleOf, when } from "../format";
import { currentUser, navigate } from "../store";
import type { SessionSummary } from "../types";
import { OwnerLabel } from "./OwnerLabel";
import { StatusChip } from "./StatusChip";
import { UploadRecording } from "./UploadRecording";

const PAGE = 100;
const POLL_MS = 5000;

/**
 * The viewer's sessions. Admins can switch to everyone's (`all`) or look at one user's
 * (`userId`, from the Admin page); rows that aren't the viewer's own name their owner.
 */
export function SessionsView({ all = false, userId }: { all?: boolean; userId?: string }) {
  const [head, setHead] = useState<SessionSummary[] | null>(null);
  const [more, setMore] = useState<SessionSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [userName, setUserName] = useState<string | null>(null);
  const isAdmin = currentUser()?.is_admin ?? false;
  const filter: ListOptions = { all, userId };

  useEffect(() => {
    let stopped = false;
    const load = async () => {
      try {
        const page = await listSessions({ ...filter, limit: PAGE, offset: 0 });
        if (stopped) return;
        setHead(page.items);
        setTotal(page.total);
        setError(null);
      } catch (e) {
        if (stopped || (e instanceof ApiError && e.status === 401)) return;
        setError(
          e instanceof ApiError && e.status === 403
            ? "Only admins can see other people's sessions."
            : "Couldn't load sessions.",
        );
      }
    };
    void load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!userId || !isAdmin) return;
    let stopped = false;
    listUsers().then(
      (users) => !stopped && setUserName(users.find((u) => u.id === userId)?.display_name ?? null),
      () => undefined,
    );
    return () => {
      stopped = true;
    };
  }, [userId]);

  async function loadMore() {
    try {
      const offset = PAGE + more.length;
      const page = await listSessions({ ...filter, limit: PAGE, offset });
      setMore((m) => [...m, ...page.items]);
    } catch {
      setError("Couldn't load more sessions.");
    }
  }

  const rows = [...(head ?? []), ...more];
  const shown = rows.filter((s) => matchesTitle(s, query));

  return (
    <section>
      <div class="toolbar">
        <h1 class="h1">Sessions</h1>
        {isAdmin && !userId && (
          <label class="toggle">
            <input
              type="checkbox"
              checked={all}
              onChange={(e) => navigate((e.target as HTMLInputElement).checked ? "/?all=1" : "/")}
            />
            All users
          </label>
        )}
        {userId && (
          <span class="filter-chip" data-testid="user-filter">
            {userName ?? head?.[0]?.owner.display_name ?? "One user"}
            <button
              class="chip-clear"
              type="button"
              aria-label="Clear user filter"
              onClick={() => navigate("/")}
            >
              ✕
            </button>
          </span>
        )}
        <input
          class="field search"
          type="search"
          placeholder="Search by title"
          aria-label="Search sessions by title"
          value={query}
          onInput={(e) => setQuery((e.target as HTMLInputElement).value)}
        />
        {!userId && <UploadRecording />}
      </div>
      {error && (
        <p class="error" role="alert">
          {error}
        </p>
      )}
      {head === null && !error && <p class="muted">Loading…</p>}
      {head !== null && rows.length === 0 && (
        <p class="muted">
          {userId
            ? "No sessions for this user."
            : all
              ? "No sessions yet."
              : "No sessions yet. Start one from the Mac app, or upload a recording."}
        </p>
      )}
      {head !== null && rows.length > 0 && shown.length === 0 && (
        <p class="muted">No sessions match “{query}”.</p>
      )}
      <ul class="rows">
        {shown.map((s) => (
          <li key={s.id}>
            <a
              class="row"
              data-testid="session-row"
              href={`/sessions/${s.id}`}
              onClick={(e) => {
                e.preventDefault();
                navigate(`/sessions/${s.id}`);
              }}
            >
              <span class="row-main">
                <span class="row-title">{titleOf(s)}</span>
                <span class="meta">
                  <OwnerLabel owner={s.owner} after=" · " />
                  {when(s.started_at)}
                  {duration(s.started_at, s.ended_at) && ` · ${duration(s.started_at, s.ended_at)}`}
                  {` · ${s.mode}`}
                </span>
              </span>
              <StatusChip status={s.status} streaming={s.streaming} />
            </a>
          </li>
        ))}
      </ul>
      {head !== null && rows.length < total && query.trim() === "" && (
        <button class="btn" onClick={loadMore}>
          Load more
        </button>
      )}
      {head !== null && rows.length < total && query.trim() !== "" && (
        <p class="muted small">
          Searching {rows.length} of {total} sessions. Clear the search to load more.
        </p>
      )}
    </section>
  );
}
