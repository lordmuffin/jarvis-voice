import { signal } from "@preact/signals";
import { useEffect, useMemo, useState } from "preact/hooks";
import { endSession, ApiError } from "../api";
import { Banner, Button, Column, Dialog, Section, Text } from "../ds/components";
import { copilotInsights, workInsights, type Filter } from "../feed";
import { clock } from "../format";
import { dismissSuggestion, loadDismissed, restoreSuggestion } from "../dismissals";
import { FeedFilter } from "../jarvis/FeedFilter";
import { Fact, SessionHeader } from "../jarvis/SessionHeader";
import { ConnectionChip } from "../jarvis/ConnectionChip";
import { dockView, type Holds } from "../jarvis/dock";
import { isMine } from "../jarvis/OwnerLabel";
import { NowDock } from "../jarvis/NowDock";
import { Timeline } from "../jarvis/Timeline";
import { useArrivals } from "../jarvis/useArrivals";
import { useLookMarker } from "../look";
import { initialState, reduce, transcriptLines, type Event } from "../reducer";
import type { SessionDetail, WorkItemPage } from "../types";
import { watchSession } from "../ws";
import { SessionTitle } from "./SessionTitle";
import { SessionWorkItems } from "./WorkItems";

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

  const [filter, setFilter] = useState<Filter>("all");
  const [work, setWork] = useState<WorkItemPage | null>(null);
  const insights = useMemo(
    () => [...copilotInsights(s.copilot), ...(work?.enabled ? workInsights(work.items) : [])],
    [s.copilot, work],
  );
  // Findings are placed after the last *final* line, so drafts (still settling) stay last.
  const settledMs = s.segments.reduce((m, x) => Math.max(m, x.end_ms), 0);
  const arrivals = useArrivals(
    [...insights.map((i) => i.key), ...s.copilot.suggestions.map((x) => `suggestion:${x.id}`)],
    settledMs,
  );
  const look = useLookMarker(settledMs);

  // Dismissals are kept in this browser (the server has no endpoint for them yet).
  const [dismissed, setDismissed] = useState(() => loadDismissed(detail.id));
  const [holds, setHolds] = useState<Holds>({});
  const version = s.copilot.version;
  useEffect(() => {
    // An expired suggestion stays visible, labelled, until the copilot's next update.
    const fresh = s.copilot.suggestions.filter(
      (x) => x.expires_at_ms <= s.streamMs && !dismissed.has(x.id) && !holds[x.id],
    );
    if (fresh.length)
      setHolds((h) => ({
        ...h,
        ...Object.fromEntries(fresh.map((x) => [x.id, { kind: "expired" as const, version }])),
      }));
  }, [s.streamMs, version]);
  const onDismiss = (id: string) => {
    dismissSuggestion(detail.id, id);
    setDismissed(loadDismissedWith(detail.id, dismissed, id));
    setHolds((h) => ({ ...h, [id]: { kind: "dismissed", version } }));
  };
  const onUndo = (id: string) => {
    restoreSuggestion(detail.id, id);
    setDismissed((d) => {
      const n = new Set(d);
      n.delete(id);
      return n;
    });
    setHolds(({ [id]: _gone, ...rest }) => rest);
  };
  const dock = dockView({
    suggestions: s.copilot.suggestions,
    dismissed,
    holds,
    version,
    streamMs: settledMs,
    arrivals,
    lookedAt: look.wallMs,
  });

  const mine = isMine(session.owner) && session.status === "live";
  const [ending, setEnding] = useState<"idle" | "confirm" | "busy">("idle");
  const [endError, setEndError] = useState<string | null>(null);
  async function end() {
    setEnding("busy");
    setEndError(null);
    try {
      dispatch({ type: "session", session: await endSession(detail.id) });
      setEnding("idle");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      setEndError(e instanceof ApiError ? e.message : "Couldn't end the session. Try again.");
      setEnding("idle");
    }
  }

  const copilotOffline = !!s.status && !s.status.llm_ok && session.streaming;
  return (
    <Column>
      <SessionHeader
        session={session}
        title={<SessionTitle session={session} onRenamed={(r) => dispatch({ type: "session", session: r })} />}
        facts={
          <>
            <Fact label="Elapsed">
              <Text size="stat" data-testid="elapsed">
                {clock(s.streamMs)}
              </Text>
            </Fact>
            <Fact label="Link">
              <ConnectionChip
                connection={s.connection}
                // The last status message goes stale once audio stops; don't show it as current.
                status={session.streaming ? s.status : null}
                copilotOffline={copilotOffline}
              />
            </Fact>
          </>
        }
        actions={
          mine && (
            <Button variant="danger-quiet" size="sm" onClick={() => setEnding("confirm")}>
              End Session
            </Button>
          )
        }
      />
      {s.error && <Banner variant="error">{s.error.message}</Banner>}
      {endError && <Banner variant="error">{endError}</Banner>}
      <Section>
        <Timeline
          live
          label="Transcript and copilot"
          lines={lines}
          markers={s.markers}
          insights={insights}
          arrivals={arrivals}
          finalNote={s.finalNote}
          finalAt={settledMs}
          filter={filter}
          look={look}
          toolbar={<FeedFilter insights={insights} value={filter} onChange={setFilter} />}
        />
        <NowDock
          view={dock}
          copilotOffline={copilotOffline}
          localOnly={session.local_only}
          onDismiss={onDismiss}
          onUndo={onUndo}
        />
      </Section>
      <SessionWorkItems sessionId={detail.id} ownerId={detail.owner.id} live onPage={setWork} />
      {ending !== "idle" && (
        <Dialog
          title="End this session?"
          onClose={() => ending === "confirm" && setEnding("idle")}
          actions={
            <>
              <Button disabled={ending === "busy"} title={ending === "busy" ? "Ending the session." : undefined} onClick={() => setEnding("idle")}>
                Keep Recording
              </Button>
              <Button
                variant="danger"
                disabled={ending === "busy"}
                title={ending === "busy" ? "Ending the session." : undefined}
                onClick={() => void end()}>
                {ending === "busy" ? "Ending…" : "End Session"}
              </Button>
            </>
          }
        >
          <Text tone="muted">Recording stops on the Mac. Jarvis writes the note from the transcript.</Text>
        </Dialog>
      )}
    </Column>
  );
}

function loadDismissedWith(sessionId: string, prev: Set<string>, id: string): Set<string> {
  // Storage may be blocked; keep what this page already knows either way.
  return new Set([...loadDismissed(sessionId), ...prev, id]);
}
