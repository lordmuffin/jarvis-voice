import { useState } from "preact/hooks";
import { ApiError, approveActions, sessionActions } from "../api";
import { needsPolling, outcomeNote, pendingActions, sortActions } from "../actions";
import { Banner, Bar, Button, Section, SectionLabel, Stack, Text } from "../ds/components";
import { ActionCard } from "../jarvis/ActionCard";
import { currentUser } from "../store";
import type { ActionPage } from "../types";
import { POLL, usePolling } from "./WorkItems";

export { POLL };

/**
 * What the copilot proposes to do in one session, for its owner to approve. Polls while the
 * session is live or something is running; renders nothing for anyone but the owner, when the
 * feature is off, or for a finished session with nothing proposed.
 */
export function SessionActions({
  sessionId,
  ownerId,
  live,
}: {
  sessionId: string;
  ownerId: string;
  live: boolean;
}) {
  const user = currentUser();
  const mine = !!user?.actions && user.id === ownerId;
  const [page, setPage] = useState<ActionPage | null>(null);
  const [reloads, setReloads] = useState(0);

  async function load(): Promise<ActionPage | null> {
    if (!mine) return null;
    try {
      const next = await sessionActions(sessionId);
      setPage(next);
      return next;
    } catch {
      return page; /* 404 or offline: keep what we had, try again on the next poll */
    }
  }

  usePolling(load, (p) => live || needsPolling(p.items), [sessionId, live, mine, reloads]);

  if (!mine || !page?.enabled) return null;
  if (!live && page.items.length === 0) return null;
  return (
    <Section aria-label="Proposed actions" data-testid="actions">
      <SectionLabel as="h3">Proposed actions</SectionLabel>
      {page.items.length === 0 ? (
        <Text as="p" size="caption" tone="muted">
          Things you ask for out loud ("remind me…", "add a task…") are proposed here. Nothing happens until you
          approve it.
        </Text>
      ) : (
        <ActionList page={page} onChanged={() => setReloads((n) => n + 1)} />
      )}
    </Section>
  );
}

/** Cards for a set of actions, with "approve all" for the pending ones. */
export function ActionList({ page, onChanged }: { page: ActionPage; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = pendingActions(page.items);

  async function approveAll() {
    setBusy(true);
    setError(null);
    try {
      const sessionId = pending[0]!.session_id;
      const res = await approveActions(
        sessionId,
        pending.map((i) => ({ id: i.id, payload_sha256: i.payload_sha256 })),
      );
      const notes = res.results.map((r) => outcomeNote(r.outcome)).filter(Boolean);
      if (notes.length) setError(`${notes.length} of ${pending.length} didn't go through: ${notes[0]}`);
      onChanged();
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) {
        setError(e instanceof ApiError ? e.message : "Couldn't approve those.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack>
      {pending.length >= 2 && (
        <Bar>
          <Button variant="primary" size="sm" disabled={busy} onClick={() => void approveAll()}>
            {busy ? "Approving…" : `✓ Approve all (${pending.length})`}
          </Button>
          <Text size="caption" tone="muted">
            They run in this order, and a failure doesn't stop the rest.
          </Text>
        </Bar>
      )}
      {error && <Banner variant="error">{error}</Banner>}
      {sortActions(page.items).map((item) => (
        <ActionCard key={item.id} item={item} onChanged={onChanged} />
      ))}
    </Stack>
  );
}
