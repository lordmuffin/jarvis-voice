import type { ComponentChildren } from "preact";
import { StatusChip } from "../ds/components";
import { when } from "../format";
import type { SessionSummary } from "../types";
import { OwnerLabel } from "./OwnerLabel";

/**
 * The block at the top of a session: what kind and when, the title (with its Rename), then
 * status and whatever the view adds (Elapsed and Link when live, the length when past).
 */
export function SessionHeader({
  session,
  title,
  facts,
  actions,
  note,
  suffix,
}: {
  session: SessionSummary;
  /** The title and its Rename control. */
  title: ComponentChildren;
  /** Extra labelled facts after Status. */
  facts?: ComponentChildren;
  actions?: ComponentChildren;
  /** Sits under the title (who owns it). */
  note?: ComponentChildren;
  /** After the date in the label, e.g. how long it ran. */
  suffix?: string;
}) {
  const kind = session.mode === "meeting" ? "Meeting" : "Solo";
  return (
    <section aria-label="Session" class="i-block d-block-grid" data-testid="session-head">
      <div class="i-stack d-grow-main">
        <span class="t-label">
          {kind} · {when(session.started_at)}
          {suffix && ` · ${suffix}`}
          {session.restricted && " · Restricted"}
        </span>
        <div class="i-row">{title}</div>
        <OwnerLabel owner={session.owner} />
        {note}
      </div>
      <div class="d-stat-row">
        <div class="d-stat">
          <span class="t-label">Status</span>
          <StatusChip status={session.status} streaming={session.streaming} />
        </div>
        {facts}
        {actions}
      </div>
    </section>
  );
}

/** One labelled fact in the header (Elapsed, Link). */
export function Fact({ label, children }: { label: string; children: ComponentChildren }) {
  return (
    <div class="d-stat">
      <span class="t-label">{label}</span>
      {children}
    </div>
  );
}
