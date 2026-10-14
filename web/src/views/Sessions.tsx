import { useEffect, useState } from "preact/hooks";
import { ApiError, listSessions, type ListOptions } from "../api";
import {
  Banner,
  Bar,
  Button,
  Choice,
  EmptyState,
  ItemMain,
  List,
  Loading,
  PageTitle,
  RowLink,
  RowMeta,
  RowTitle,
  SearchInput,
  Section,
  StatusChip,
  Text,
} from "../ds/components";
import { duration, matchesTitle, titleOf, when } from "../format";
import { currentUser, navigate } from "../store";
import type { SessionSummary } from "../types";
import { OwnerLabel } from "./OwnerLabel";
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
    <Section>
      <Bar>
        <PageTitle>Sessions</PageTitle>
        {isAdmin && !userId && (
          <Choice
            label="All users"
            checked={all}
            onChange={(e) => navigate((e.target as HTMLInputElement).checked ? "/?all=1" : "/")}
          />
        )}
        <SearchInput
          placeholder="Search by title"
          aria-label="Search sessions by title"
          value={query}
          onInput={(e) => setQuery((e.target as HTMLInputElement).value)}
        />
        {!userId && <UploadRecording />}
      </Bar>
      {error && <Banner variant="error">{error}</Banner>}
      {head === null && !error && <Loading />}
      {head !== null && rows.length === 0 && (
        <EmptyState>
          {userId
            ? "No sessions for this user."
            : all
              ? "No sessions yet."
              : "No sessions yet. Start one from the Mac app, or upload a recording."}
        </EmptyState>
      )}
      {head !== null && rows.length > 0 && shown.length === 0 && (
        <EmptyState>No sessions match “{query}”.</EmptyState>
      )}
      <List>
        {shown.map((s) => (
          <li key={s.id}>
            <RowLink
              data-testid="session-row"
              href={`/sessions/${s.id}`}
              onClick={(e) => {
                e.preventDefault();
                navigate(`/sessions/${s.id}`);
              }}
            >
              <ItemMain>
                <RowTitle>{titleOf(s)}</RowTitle>
                <RowMeta>
                  <OwnerLabel owner={s.owner} after=" · " />
                  {when(s.started_at)}
                  {duration(s.started_at, s.ended_at) && ` · ${duration(s.started_at, s.ended_at)}`}
                  {` · ${s.mode}`}
                </RowMeta>
              </ItemMain>
              <StatusChip status={s.status} streaming={s.streaming} />
            </RowLink>
          </li>
        ))}
      </List>
      {head !== null && rows.length < total && query.trim() === "" && (
        <div>
          <Button onClick={loadMore}>Load more</Button>
        </div>
      )}
      {head !== null && rows.length < total && query.trim() !== "" && (
        <Text size="caption" tone="muted">
          Searching {rows.length} of {total} sessions. Clear the search to load more.
        </Text>
      )}
    </Section>
  );
}
