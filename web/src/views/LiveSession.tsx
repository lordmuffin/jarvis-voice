import { signal } from "@preact/signals";
import { useEffect, useMemo } from "preact/hooks";
import { Banner, Head, Section, StatusChip, Text } from "../ds/components";
import { initialState, reduce, transcriptLines, type Event } from "../reducer";
import type { SessionDetail } from "../types";
import { watchSession } from "../ws";
import { CopilotPanel } from "./CopilotPanel";
import { OwnerLabel } from "./OwnerLabel";
import { Panes } from "./Panes";
import { SessionTitle } from "./SessionTitle";
import { Transcript } from "./Transcript";
import { SessionWorkItems } from "./WorkItems";

const CONNECTION_LABEL = {
  idle: "Idle",
  connecting: "Connecting…",
  open: "Connected",
  closed: "Reconnecting…",
} as const;

export function LiveSession({
  detail,
  onFinished,
}: {
  detail: SessionDetail;
  onFinished: (d: SessionDetail) => void;
}) {
  const state = useMemo(
    () => signal(reduce(initialState, { type: "snapshot", detail })),
    [detail.id],
  );

  const dispatch = (e: Event) => {
    state.value = reduce(state.value, e);
  };

  useEffect(() => {
    return watchSession({
      sessionId: detail.id,
      dispatch,
      onFinished,
    });
  }, [detail.id]);

  const s = state.value;
  const lines = transcriptLines(s);
  // Title, status and streaming follow the REST polls in watchSession, not the first load.
  const session = s.session ?? detail;

  return (
    <Section>
      <Head data-testid="session-head">
        <SessionTitle session={session} onRenamed={(r) => dispatch({ type: "session", session: r })} />
        <StatusChip status={session.status} streaming={session.streaming} />
        <OwnerLabel owner={session.owner} />
        <Text size="meta" tone="muted" data-testid="connection">
          {CONNECTION_LABEL[s.connection]}
        </Text>
        {/* The last status message goes stale once audio stops; don't show it as current. */}
        {s.status && session.streaming && (
          <Text size="meta" tone="muted">
            {s.status.stt_tier ?? "no STT"} · lag {s.status.lag_ms} ms
            {!s.status.llm_ok && " · copilot offline"}
          </Text>
        )}
      </Head>
      {s.error && <Banner variant="error">{s.error.message}</Banner>}
      <Panes
        panes={[
          {
            id: "transcript",
            label: "Transcript",
            content: <Transcript lines={lines} markers={s.markers} follow />,
          },
          {
            id: "copilot",
            label: "Copilot",
            content: (
              <>
                <CopilotPanel copilot={s.copilot} streamMs={s.streamMs} finalNote={s.finalNote} />
                <SessionWorkItems sessionId={detail.id} ownerId={detail.owner.id} live />
              </>
            ),
          },
        ]}
      />
    </Section>
  );
}
