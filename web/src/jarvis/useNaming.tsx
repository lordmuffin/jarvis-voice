import { useState } from "preact/hooks";
import { me } from "../store";
import type { SpeakerInfo } from "../types";
import { NameSpeaker } from "./NameSpeaker";

/**
 * Naming a voice from its tag in the transcript. Only the session's owner may (the server holds
 * the voice prints per user), so `onName` is undefined for anyone else, an admin included.
 * `refresh` loads the session again once a name is saved.
 */
export function useNaming(
  sessionId: string,
  ownerId: string,
  speakers: Record<string, SpeakerInfo>,
  refresh: () => void,
) {
  const user = me.value;
  const mine = typeof user === "object" && user !== null && user.id === ownerId;
  const [naming, setNaming] = useState<string | null>(null);
  const speaker = naming ? speakers[naming] : undefined;
  return {
    onName: mine ? setNaming : undefined,
    dialog: speaker ? (
      <NameSpeaker
        sessionId={sessionId}
        speaker={speaker}
        onSaved={refresh}
        onClose={() => setNaming(null)}
      />
    ) : null,
  };
}
