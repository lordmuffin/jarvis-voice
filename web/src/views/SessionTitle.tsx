import { useEffect, useRef, useState } from "preact/hooks";
import { ApiError, renameSession } from "../api";
import { Button, Form, Input, Text } from "../ds/components";
import { titleOf } from "../format";
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
  const form = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (editing) form.current?.querySelector("input")?.select();
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
      onRenamed(await renameSession(session.id, draft));
      setEditing(false);
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return; // signed out: Login replaces this
      setError("Couldn't rename. Try again.");
    } finally {
      setSaving(false);
    }
  }

  if (!editing)
    return (
      <>
        <Text as="h1" size="body-lg" strong data-testid="session-title">
          {titleOf(session)}
        </Text>
        <Button variant="quiet" size="sm" onClick={start} aria-label="Rename session">
          Rename
        </Button>
      </>
    );

  return (
    <Form
      inline
      grow
      elRef={(el) => {
        form.current = el;
      }}
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <Input
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
      <Button variant="primary" size="sm" type="submit" disabled={saving}>
        Save
      </Button>
      <Button variant="quiet" size="sm" onClick={() => setEditing(false)}>
        Cancel
      </Button>
      {error && (
        <Text size="caption" tone="error" role="alert">
          {error}
        </Text>
      )}
    </Form>
  );
}
