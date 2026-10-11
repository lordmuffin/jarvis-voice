import { audioUrl } from "../api";
import { duration, when } from "../format";
import { initialState, reduce, transcriptLines } from "../reducer";
import type { Channel, SessionDetail, SessionSummary } from "../types";
import { NotePane } from "./NotePane";
import { OwnerLabel } from "./OwnerLabel";
import { Panes } from "./Panes";
import { SessionTitle } from "./SessionTitle";
import { StatusChip } from "./StatusChip";
import { Transcript } from "./Transcript";

// Same names as the transcript: the mic is the local speaker, system audio everyone else.
const CHANNEL_LABEL: Record<Channel, string> = { mic: "Me", system: "Them" };

/** One player per channel that has a recording on the server. */
export function Recordings({ detail }: { detail: SessionDetail }) {
  const channels = detail.channels.filter((c) => detail.audio?.[c]);
  if (channels.length === 0) return null;
  return (
    <section class="recordings" aria-label="Recordings">
      {channels.map((c) => (
        <div class={`recording recording-${c}`} key={c} data-testid="recording">
          <span class="recording-label" id={`rec-${c}`}>
            {CHANNEL_LABEL[c]}
          </span>
          <audio
            controls
            preload="none"
            src={audioUrl(detail.id, c)}
            aria-labelledby={`rec-${c}`}
          />
        </div>
      ))}
    </section>
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
  ) : detail.status === "failed" ? (
    <p class="error">This session failed before a note was written.</p>
  ) : detail.local_only ? (
    <p class="muted">Local-only session: no note was generated.</p>
  ) : (
    <p class="muted">No note was written for this session.</p>
  );

  return (
    <section>
      <div class="session-head">
        <SessionTitle session={detail} onRenamed={onRenamed} />
        <StatusChip status={detail.status} streaming={detail.streaming} />
        <OwnerLabel owner={detail.owner} />
        <span class="meta">
          {when(detail.started_at)}
          {duration(detail.started_at, detail.ended_at) &&
            ` · ${duration(detail.started_at, detail.ended_at)}`}
        </span>
      </div>
      <Recordings detail={detail} />
      <Panes
        panes={[
          { id: "note", label: "Note", content: noteContent },
          {
            id: "transcript",
            label: "Transcript",
            content: <Transcript lines={lines} markers={state.markers} />,
          },
        ]}
      />
    </section>
  );
}
