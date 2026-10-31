import { useState } from "preact/hooks";
import { ApiError, nameSpeaker } from "../api";
import { Button, Choice, Dialog, Field, Input } from "../ds/components";
import type { SpeakerInfo } from "../types";

/**
 * Who a voice is. Naming it also remembers the voice, so later sessions recognise it; "This is
 * me" makes it the signed-in user's own voice, whose lines read Me.
 */
export function NameSpeaker({
  sessionId,
  speaker,
  onSaved,
  onClose,
}: {
  sessionId: string;
  speaker: SpeakerInfo;
  /** The name was stored: load the session again. */
  onSaved: () => void;
  onClose: () => void;
}) {
  const [label, setLabel] = useState(speaker.label ?? "");
  const [owner, setOwner] = useState(speaker.is_owner);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const name = label.trim();
  const was =
    speaker.label ??
    (speaker.ordinal ? `Speaker ${speaker.ordinal}` : "this voice");

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await nameSpeaker(sessionId, speaker.id, {
        label: name,
        is_owner: owner,
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setError(e instanceof ApiError ? e.message : "Couldn't save. Try again.");
      setBusy(false);
      return;
    }
    onSaved();
    onClose();
  }

  return (
    <Dialog
      title={`Who is ${was}?`}
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!name || busy}
            onClick={() => void save()}
          >
            Save
          </Button>
        </>
      }
    >
      <Field
        label="Name"
        htmlFor="speaker-name"
        error={error}
        help="Remembered, so this voice is recognised next time."
      >
        <Input
          id="speaker-name"
          value={label}
          maxLength={100}
          autoFocus
          onInput={(e) => setLabel((e.target as HTMLInputElement).value)}
        />
      </Field>
      <Choice
        label="This is me"
        checked={owner}
        onChange={(e) => setOwner((e.target as HTMLInputElement).checked)}
      />
    </Dialog>
  );
}
