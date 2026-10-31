import { useEffect, useState } from "preact/hooks";
import { ApiError, listSessions, restoreSession } from "../api";
import { Banner, Button, EmptyState, List, Loading, PageTitle, Section, Text } from "../ds/components";
import { duration, titleOf, when } from "../format";
import { OwnerLabel } from "../jarvis/OwnerLabel";
import { navigate } from "../store";
import type { SessionSummary } from "../types";

const PAGE = 100;

/** Recently deleted: the caller's sessions in the trash, each with Restore. */
export function DeletedView() {
  const [rows, setRows] = useState<SessionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    listSessions({ deleted: true, limit: PAGE }).then(
      (page) => !stopped && setRows(page.items),
      (e) => {
        if (stopped || (e instanceof ApiError && e.status === 401)) return;
        setError("Couldn't load deleted sessions.");
      },
    );
    return () => {
      stopped = true;
    };
  }, []);

  async function restore(s: SessionSummary) {
    setBusy(s.id);
    setError(null);
    try {
      await restoreSession(s.id);
      setRows((r) => (r ?? []).filter((x) => x.id !== s.id));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError(`Couldn't restore “${titleOf(s)}”. Try again.`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Section>
      <PageTitle>Recently Deleted</PageTitle>
      {error && <Banner variant="error">{error}</Banner>}
      {rows === null && !error && <Loading />}
      {rows !== null && rows.length === 0 && (
        <EmptyState hint="A deleted session stays here, with its recording, until it is removed for good.">
          Nothing deleted.
        </EmptyState>
      )}
      <List>
        {(rows ?? []).map((s) => (
          <li key={s.id} data-testid="deleted-row" class="i-row">
            <a
              class="d-item-main"
              href={`/sessions/${s.id}`}
              onClick={(e) => {
                e.preventDefault();
                navigate(`/sessions/${s.id}`);
              }}
            >
              <span class={s.title?.trim() ? "ti" : "ti ti--none"}>{titleOf(s)}</span>
              <span class="mt">
                <OwnerLabel owner={s.owner} after=" · " />
                {when(s.started_at)}
                {duration(s.started_at, s.ended_at) && ` · ${duration(s.started_at, s.ended_at)}`}
              </span>
              <Text size="caption" tone="muted">
                {s.deleted_at && `Deleted ${when(s.deleted_at)}`}
                {s.trash_expires_at && ` · Removed for good ${when(s.trash_expires_at)}`}
              </Text>
            </a>
            <Button size="sm" disabled={busy === s.id} onClick={() => void restore(s)}>
              Restore
            </Button>
          </li>
        ))}
      </List>
    </Section>
  );
}
