import { useState } from "preact/hooks";
import { ApiError, getSession, unrestrictSession } from "../api";
import { Button, Reason } from "../ds/components";
import { me } from "../store";
import type { SessionDetail } from "../types";

/**
 * "Remove restriction" on a work call, for its owner. A restricted session's note is filed apart
 * and never published; removing the restriction moves the note out of `Restricted/` and lets it
 * be published like any other. The session is loaded again and handed to `onChanged`.
 */
export function UnrestrictControl({
  detail,
  onChanged,
}: {
  detail: SessionDetail;
  onChanged: (d: SessionDetail) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const user = me.value;
  const owner = user !== null && user !== "loading" && user.id === detail.owner.id;
  if (!detail.restricted || !owner) return null;

  async function lift() {
    setBusy(true);
    setError(null);
    try {
      await unrestrictSession(detail.id);
      onChanged(await getSession(detail.id));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError(e instanceof ApiError ? e.message : "Couldn't remove the restriction. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div class="i-row">
      <Button
        variant="secondary"
        size="sm"
        disabled={busy}
        title="Move the note out of Restricted and allow publishing it"
        onClick={lift}
      >
        Remove restriction
      </Button>
      {error && <Reason>{error}</Reason>}
    </div>
  );
}
