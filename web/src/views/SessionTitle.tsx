import { useEffect, useRef, useState } from "preact/hooks";
import { ApiError, renameSession } from "../api";
import { titleOf } from "../format";
import { signOut, token } from "../store";
import type { SessionSummary } from "../types";

/** The session heading, with an inline rename. `onRenamed` gets the server's updated row. */
export function SessionTitle({
  session,
  onRenamed,
}: {
  session: SessionSummary;
  onRenamed: (s: SessionSummary) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) input.current?.select();
  }, [editing]);

  function start() {
    setDraft(session.title ?? "");
    setError(null);
    setEditing(true);
  }

  async function save() {
    if (draft.trim() === (session.title ?? "").trim()) return setEditing(false);
    setSaving(true);
    try {
      onRenamed(await renameSession(token.value ?? "", session.id, draft));
      setEditing(false);
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return signOut();
      setError("Couldn't rename. Try again.");
    } finally {
      setSaving(false);
    }
  }

  if (!editing)
    return (
      <>
        <h1 class="h1">{titleOf(session)}</h1>
        <button class="btn btn-quiet btn-sm" onClick={start} aria-label="Rename session">
          Rename
        </button>
      </>
    );

  return (
    <form
      class="rename"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <input
        ref={input}
        class="field rename-field"
        aria-label="Session title"
        placeholder="Untitled session"
        maxLength={200}
        value={draft}
        disabled={saving}
        onInput={(e) => setDraft((e.target as HTMLInputElement).value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setEditing(false);
        }}
      />
      <button class="btn btn-sm" type="submit" disabled={saving}>
        Save
      </button>
      <button class="btn btn-quiet btn-sm" type="button" onClick={() => setEditing(false)}>
        Cancel
      </button>
      {error && (
        <span class="error small" role="alert">
          {error}
        </span>
      )}
    </form>
  );
}
