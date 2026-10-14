import { audioUrl } from "../api";
import { Head, Player, Recordings as RecordingList, Section, Text } from "../ds/components";
import { duration, when } from "../format";
import { initialState, reduce, transcriptLines } from "../reducer";
import type { Channel, SessionDetail, SessionSummary } from "../types";
import { NotePane } from "./NotePane";
import { OwnerLabel } from "./OwnerLabel";
import { Panes } from "./Panes";
import { SessionTitle } from "./SessionTitle";
import { StatusChip } from "../ds/components";
import { Transcript } from "./Transcript";
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
  ) : detail.status === "failed" ? (
    <Text as="p" tone="error">
      This session failed before a note was written.
    </Text>
  ) : detail.local_only ? (
    <Text as="p" tone="muted">
      Local-only session: no note was generated.
    </Text>
  ) : (
    <Text as="p" tone="muted">
      No note was written for this session.
    </Text>
  );

  return (
    <Section>
      <Head data-testid="session-head">
        <SessionTitle session={detail} onRenamed={onRenamed} />
        <StatusChip status={detail.status} streaming={detail.streaming} />
        <OwnerLabel owner={detail.owner} />
        <Text size="meta" tone="muted">
          {when(detail.started_at)}
          {duration(detail.started_at, detail.ended_at) &&
            ` · ${duration(detail.started_at, detail.ended_at)}`}
        </Text>
      </Head>
      <Recordings detail={detail} />
      <SessionWorkItems sessionId={detail.id} ownerId={detail.owner.id} live={false} />
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
    </Section>
  );
}
