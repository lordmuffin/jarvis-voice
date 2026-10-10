import { duration, when } from "../format";
import { initialState, reduce, transcriptLines } from "../reducer";
import type { SessionDetail, SessionSummary } from "../types";
import { NoteView } from "./NoteView";
import { Panes } from "./Panes";
import { SessionTitle } from "./SessionTitle";
import { StatusChip } from "./StatusChip";
import { Transcript } from "./Transcript";

export function PastSession({
  detail,
  onRenamed,
}: {
  detail: SessionDetail;
  onRenamed: (s: SessionSummary) => void;
}) {
  const state = reduce(initialState, { type: "snapshot", detail });
  const lines = transcriptLines(state);
  const note = detail.final_note;

  const noteContent = note?.markdown ? (
    <NoteView markdown={note.markdown} />
  ) : note ? (
    <p class="muted">The note “{note.title}” is no longer available on the server.</p>
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
        <span class="meta">
          {when(detail.started_at)}
          {duration(detail.started_at, detail.ended_at) &&
            ` · ${duration(detail.started_at, detail.ended_at)}`}
        </span>
      </div>
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
