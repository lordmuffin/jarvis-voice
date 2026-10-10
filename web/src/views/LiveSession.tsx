import { signal } from "@preact/signals";
import { useEffect, useMemo } from "preact/hooks";
import { initialState, reduce, transcriptLines, type Event } from "../reducer";
import { signOut, token } from "../store";
import type { SessionDetail } from "../types";
import { watchSession } from "../ws";
import { CopilotPanel } from "./CopilotPanel";
import { Panes } from "./Panes";
import { StatusChip } from "./StatusChip";
import { titleOf } from "../format";
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

  useEffect(() => {
    const dispatch = (e: Event) => {
      state.value = reduce(state.value, e);
    };
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

  return (
    <section>
      <div class="session-head">
        <h1 class="h1">{titleOf(detail)}</h1>
        <StatusChip status={detail.status} />
        <span class="meta" data-testid="connection">
          {CONNECTION_LABEL[s.connection]}
        </span>
        {s.status && (
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
