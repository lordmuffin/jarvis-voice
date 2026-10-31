import { useState } from "preact/hooks";
import { ApiError, deleteSession, restoreSession, setDeleteAfter } from "../api";
import { Banner, Button, Dialog, Select, Text } from "../ds/components";
import { when } from "../format";
import type { SessionSummary } from "../types";

const AFTER_DAYS = [1, 7, 30, 90];

/** Delete: after a confirm, the session moves to Recently deleted. Not offered while recording. */
export function DeleteSession({ detail, onDeleted }: { detail: SessionSummary; onDeleted: () => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (detail.status === "live" || detail.status === "finalizing") return null;

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await deleteSession(detail.id);
      setAsking(false);
      onDeleted();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError(e instanceof ApiError && e.status === 409 ? e.message : "Couldn't delete. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button variant="danger-quiet" size="sm" onClick={() => setAsking(true)}>
        Delete
      </Button>
      {error && !asking && <Banner variant="error">{error}</Banner>}
      {asking && (
        <Dialog
          title="Delete this session?"
          onClose={() => !busy && setAsking(false)}
          actions={
            <>
              <Button variant="quiet" disabled={busy} onClick={() => setAsking(false)}>
                Cancel
              </Button>
              <Button variant="danger" disabled={busy} onClick={() => void confirm()}>
                {busy ? "Deleting…" : "Delete Session"}
              </Button>
            </>
          }
        >
          <Text>
            It moves to Recently deleted, with its recording and transcript. You can restore it from there until
            it is removed for good.
          </Text>
          {error && <Banner variant="error">{error}</Banner>}
        </Dialog>
      )}
    </>
  );
}

/** "Delete after": have the server move this session to Recently deleted in N days. */
export function DeleteAfter({
  detail,
  onChanged,
}: {
  detail: SessionSummary;
  onChanged: (s: SessionSummary) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function choose(value: string) {
    setBusy(true);
    setError(null);
    try {
      onChanged(await setDeleteAfter(detail.id, value === "" ? null : Number(value)));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError("Couldn't save. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <label>
        <Text as="span" size="caption" tone="muted">
          Delete after{" "}
        </Text>
        <Select
          aria-label="Delete after"
          disabled={busy}
          value=""
          onChange={(e) => void choose((e.target as HTMLSelectElement).value)}
        >
          <option value="">{detail.purge_after ? "Cancel" : "Never"}</option>
          {AFTER_DAYS.map((d) => (
            <option key={d} value={d}>
              {d === 1 ? "1 day" : `${d} days`}
            </option>
          ))}
        </Select>
      </label>
      {detail.purge_after && (
        <Text size="caption" tone="muted">
          Deletes {when(detail.purge_after)}
        </Text>
      )}
      {error && <Banner variant="error">{error}</Banner>}
    </>
  );
}

/** On a session that is in Recently deleted: say so and offer Restore. */
export function DeletedNotice({
  detail,
  onRestored,
}: {
  detail: SessionSummary;
  onRestored: (s: SessionSummary) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!detail.deleted_at) return null;

  async function restore() {
    setBusy(true);
    setError(null);
    try {
      onRestored(await restoreSession(detail.id));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError("Couldn't restore. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Banner
      variant="warning"
      title="In Recently deleted. "
      action={
        <Button size="sm" disabled={busy} onClick={() => void restore()}>
          Restore
        </Button>
      }
    >
      {detail.trash_expires_at ? `Removed for good ${when(detail.trash_expires_at)}.` : "It will be removed for good."}
      {error && ` ${error}`}
    </Banner>
  );
}
