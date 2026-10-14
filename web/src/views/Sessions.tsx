import { useEffect, useRef, useState } from "preact/hooks";
import { ApiError, listSessions, type ListOptions } from "../api";
import {
  Banner,
  Button,
  Choice,
  EmptyState,
  List,
  Loading,
  PageTitle,
  Section,
  Stack,
  Text,
} from "../ds/components";
import { PinnedLiveRow } from "../jarvis/PinnedLiveRow";
import { SessionFilters } from "../jarvis/SessionFilters";
import { SessionRow } from "../jarvis/SessionRow";
import {
  filtersToSearch,
  isFiltered,
  parseFilters,
  toListOptions,
  type Filters,
} from "../sessionFilters";
import { PLANNED } from "../planned";
import { currentUser, navigate } from "../store";
import type { SessionStatus, SessionSummary } from "../types";
import { useDebounced } from "../useDebounced";
import { UploadRecording } from "./UploadRecording";

const PAGE = 100;
const POLL_MS = 5000;
const SEARCH_MS = 250;

/**
 * The viewer's sessions, narrowed and ordered by the server (status, mode, date, sort, and a
 * search of titles or transcripts). The filters live in the URL query so a view can be shared.
 * Admins can switch to everyone's (`all`) or look at one user's (`userId`, from the Admin
 * page); rows that aren't the viewer's own name their owner.
 */
export function SessionsView({ all = false, userId }: { all?: boolean; userId?: string }) {
  const [head, setHead] = useState<SessionSummary[] | null>(null);
  const [more, setMore] = useState<SessionSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<SessionStatus, number> | null>(null);
  const [filters, setFilters] = useState<Filters>(() => parseFilters(location.search));
  const [error, setError] = useState<string | null>(null);
  const isAdmin = currentUser()?.is_admin ?? false;

  // The search waits for you to stop typing; everything else applies at once.
  const q = useDebounced(filters.q, SEARCH_MS);
  const applied: Filters = { ...filters, q };
  const key = JSON.stringify([all, userId, applied]);
  const options = (): ListOptions => ({ all, userId, ...toListOptions(applied) });

  useEffect(() => {
    // Keep the address in step (without a history entry per change).
    const next = filtersToSearch(applied, location.search);
    if (next !== location.search) history.replaceState(null, "", location.pathname + next);
  }, [key]);

  const loadId = useRef(0);
  useEffect(() => {
    let stopped = false;
    const id = ++loadId.current;
    setMore([]);
    const load = async () => {
      try {
        const page = await listSessions({ ...options(), limit: PAGE, offset: 0 });
        if (stopped || id !== loadId.current) return;
        setHead(page.items);
        setTotal(page.total);
        setCounts(page.counts);
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
  }, [key]);

  async function loadMore() {
    try {
      const offset = PAGE + more.length;
      const page = await listSessions({ ...options(), limit: PAGE, offset });
      setMore((m) => [...m, ...page.items]);
    } catch {
      setError("Couldn't load more sessions.");
    }
  }

  const rows = [...(head ?? []), ...more];
  const filtered = isFiltered(applied);
  const scope = userId ? "One user" : all ? "All users" : "Your sessions";
  const clear = () => setFilters({ ...filters, status: "all", mode: "any", date: "any", q: "" });

  return (
    <Section>
      <Stack>
        {head !== null && (
          <Text size="label" data-testid="session-count">
            {scope} · {total} {total === 1 ? "session" : "sessions"}
          </Text>
        )}
        <PageTitle>Sessions</PageTitle>
      </Stack>
      <SessionFilters filters={filters} counts={counts} onChange={setFilters}>
        {isAdmin && !userId && (
          <Choice
            label="All users"
            checked={all}
            onChange={(e) =>
              navigate(
                (e.target as HTMLInputElement).checked
                  ? `/${filtersToSearch(filters, "?all=1")}`
                  : `/${filtersToSearch(filters, "")}`,
              )
            }
          />
        )}
        {!userId && <UploadRecording />}
      </SessionFilters>
      {error && <Banner variant="error">{error}</Banner>}
      {head === null && !error && <Loading />}
      {head !== null && rows.length === 0 && !filtered && (
        <EmptyState
          hint={!userId && !all ? "Start one from the Mac app, or upload a recording." : undefined}
        >
          {userId ? "No sessions for this user." : "No sessions yet."}
        </EmptyState>
      )}
      {head !== null && rows.length === 0 && filtered && (
        <EmptyState
          action={
            <Button variant="quiet" size="sm" onClick={clear}>
              {applied.q.trim() && !isFiltered({ ...applied, q: "" }) ? "Clear Search" : "Clear Filters"}
            </Button>
          }
        >
          {applied.q.trim() && !isFiltered({ ...applied, q: "" })
            ? `No sessions match “${applied.q.trim()}”.`
            : "No sessions match these filters."}
        </EmptyState>
      )}
      {PLANNED.pinnedLive && !filtered && <PinnedLive rows={rows} />}
      <List>
        {rows.map((s) => (
          <li key={s.id}>
            <SessionRow session={s} onOpen={navigate} />
          </li>
        ))}
      </List>
      {head !== null && rows.length > 0 && (
        <Text size="caption" tone="muted" data-testid="shown-count">
          {rows.length} of {total} shown
        </Text>
      )}
      {head !== null && rows.length < total && (
        <div>
          <Button onClick={loadMore}>Load more</Button>
        </div>
      )}
    </Section>
  );
}


/** The planned pinned live card: the first session that is live and streaming (see planned.ts). */
function PinnedLive({ rows }: { rows: SessionSummary[] }) {
  const live = rows.find((s) => s.status === "live" && s.streaming);
  if (!live) return null;
  return (
    <PinnedLiveRow
      session={live}
      elapsedMs={Date.now() - new Date(live.started_at).getTime()}
      status={null}
      onOpen={navigate}
    />
  );
}
