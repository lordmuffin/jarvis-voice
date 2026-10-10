import { useEffect, useState } from "preact/hooks";
import { ApiError, listSessions } from "../api";
import { duration, matchesTitle, titleOf, when } from "../format";
import { navigate, signOut, token } from "../store";
import type { SessionSummary } from "../types";
import { StatusChip } from "./StatusChip";

const PAGE = 100;
const POLL_MS = 5000;

export function SessionsView() {
  const [head, setHead] = useState<SessionSummary[] | null>(null);
  const [more, setMore] = useState<SessionSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    const load = async () => {
      try {
        const page = await listSessions(token.value ?? "", PAGE, 0);
        if (stopped) return;
        setHead(page.items);
        setTotal(page.total);
        setError(null);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return signOut();
        if (!stopped) setError("Couldn't load sessions.");
      }
    };
    void load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);

  async function loadMore() {
    try {
      const offset = PAGE + more.length;
      const page = await listSessions(token.value ?? "", PAGE, offset);
      setMore((m) => [...m, ...page.items]);
    } catch {
      setError("Couldn't load more sessions.");
    }
  }

  const all = [...(head ?? []), ...more];
  const shown = all.filter((s) => matchesTitle(s, query));

  return (
    <section>
      <div class="toolbar">
        <h1 class="h1">Sessions</h1>
        <input
          class="field search"
          type="search"
          placeholder="Search by title"
          aria-label="Search sessions by title"
          value={query}
          onInput={(e) => setQuery((e.target as HTMLInputElement).value)}
        />
      </div>
      {error && (
        <p class="error" role="alert">
          {error}
        </p>
      )}
      {head === null && !error && <p class="muted">Loading…</p>}
      {head !== null && all.length === 0 && (
        <p class="muted">No sessions yet. Start one from the Mac app.</p>
      )}
      {head !== null && all.length > 0 && shown.length === 0 && (
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
                  {when(s.started_at)}
                  {duration(s.started_at, s.ended_at) && ` · ${duration(s.started_at, s.ended_at)}`}
                  {` · ${s.mode}`}
                </span>
              </span>
              <StatusChip status={s.status} />
            </a>
          </li>
        ))}
      </ul>
      {head !== null && all.length < total && query.trim() === "" && (
        <button class="btn" onClick={loadMore}>
          Load more
        </button>
      )}
      {head !== null && all.length < total && query.trim() !== "" && (
        <p class="muted small">
          Searching {all.length} of {total} sessions. Clear the search to load more.
        </p>
      )}
    </section>
  );
}
