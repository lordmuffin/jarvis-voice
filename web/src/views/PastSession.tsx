import { audioUrl } from "../api";
import { Column, Player, Recordings as RecordingList } from "../ds/components";
import { duration } from "../format";
import { NoNote } from "../jarvis/NoNote";
import { ProcessControl } from "../jarvis/ProcessControl";
import { SessionHeader } from "../jarvis/SessionHeader";
import { Timeline } from "../jarvis/Timeline";
import { initialState, reduce, transcriptLines } from "../reducer";
import type { Channel, SessionDetail, SessionSummary } from "../types";
import { NotePane } from "./NotePane";
import { Panes } from "./Panes";
import { SessionTitle } from "./SessionTitle";
import { SessionActions } from "./SessionActions";
import { SessionWorkItems } from "./WorkItems";

// Same names as the transcript: the mic is the local speaker, system audio everyone else.
const CHANNEL_LABEL: Record<Channel, string> = { mic: "Me", system: "Them" };

/** One player per channel that has a recording on the server. */
export function Recordings({ detail }: { detail: SessionDetail }) {
  const channels = detail.channels.filter((c) => detail.audio?.[c]);
  if (channels.length === 0) return null;
  return (
    <RecordingList>
      {channels.map((c) => (
        <Player key={c} label={CHANNEL_LABEL[c]} labelId={`rec-${c}`} them={c === "system"} data-testid="recording">
          <audio controls preload="none" src={audioUrl(detail.id, c)} aria-labelledby={`rec-${c}`} />
        </Player>
      ))}
    </RecordingList>
  );
}

export function PastSession({
  detail,
  onRenamed,
  onChanged,
}: {
  detail: SessionDetail;
  onRenamed: (s: SessionSummary) => void;
  /** The note or its publication changed (the server's updated session). */
  onChanged: (d: SessionDetail) => void;
}) {
  const state = reduce(initialState, { type: "snapshot", detail });
  const lines = transcriptLines(state);
  const note = detail.final_note;

  const noteContent = note ? (
    <NotePane detail={detail} onChanged={onChanged} />
  ) : (
    <NoNote reason={detail.status === "failed" ? "failed" : detail.local_only ? "local" : "none"} />
  );

  return (
    <Column wide>
      <SessionHeader
        session={detail}
        suffix={duration(detail.started_at, detail.ended_at) || undefined}
        title={<SessionTitle session={detail} onRenamed={onRenamed} />}
        actions={<ProcessControl detail={detail} onChanged={onChanged} />}
      />
      <Recordings detail={detail} />
      <SessionActions sessionId={detail.id} ownerId={detail.owner.id} live={false} />
      <SessionWorkItems sessionId={detail.id} ownerId={detail.owner.id} live={false} />
      <Panes
        panes={[
          { id: "note", label: "Note", content: noteContent },
          {
            id: "transcript",
            label: "Transcript",
            content: <Timeline label="Transcript text" lines={lines} markers={state.markers} />,
          },
        ]}
      />
    </Column>
  );
}
