import { useEffect, useState } from "preact/hooks";
import { ApiError, listSessions, restoreSession } from "../api";
import { Banner, EmptyState, List, Loading, PageTitle, Section } from "../ds/components";
import { titleOf } from "../format";
import { DeletedRow } from "../jarvis/DeletedRow";
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
          <li key={s.id}>
            <DeletedRow
              session={s}
              busy={busy === s.id}
              onOpen={(id) => navigate(`/sessions/${id}`)}
              onRestore={(row) => void restore(row)}
            />
          </li>
        ))}
      </List>
    </Section>
  );
}
