import { signal } from "@preact/signals";
import { useEffect, useMemo } from "preact/hooks";
import { initialState, reduce, transcriptLines, type Event } from "../reducer";
import { signOut, token } from "../store";
import type { SessionDetail } from "../types";
import { watchSession } from "../ws";
import { CopilotPanel } from "./CopilotPanel";
import { Panes } from "./Panes";
import { SessionTitle } from "./SessionTitle";
import { StatusChip } from "./StatusChip";
import { Transcript } from "./Transcript";

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
      token: token.value ?? "",
      dispatch,
      onFinished,
      onUnauthorized: signOut,
    });
  }, [detail.id]);

  const s = state.value;
  const lines = transcriptLines(s);
  // Title, status and streaming follow the REST polls in watchSession, not the first load.
  const session = s.session ?? detail;

  return (
    <section>
      <div class="session-head">
        <SessionTitle session={session} onRenamed={(r) => dispatch({ type: "session", session: r })} />
        <StatusChip status={session.status} streaming={session.streaming} />
        <span class="meta" data-testid="connection">
          {CONNECTION_LABEL[s.connection]}
        </span>
        {/* The last status message goes stale once audio stops; don't show it as current. */}
        {s.status && session.streaming && (
          <span class="meta">
            {s.status.stt_tier ?? "no STT"} · lag {s.status.lag_ms} ms
            {!s.status.llm_ok && " · copilot offline"}
          </span>
        )}
      </div>
      {s.error && (
        <p class="error" role="alert">
          {s.error.message}
        </p>
      )}
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
            content: <CopilotPanel copilot={s.copilot} streamMs={s.streamMs} finalNote={s.finalNote} />,
          },
        ]}
      />
    </section>
  );
}
