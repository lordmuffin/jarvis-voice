import { RowLink } from "../ds/components";
import { StatusChip } from "../ds/components";
import { duration, titleOf, when } from "../format";
import type { SessionSummary } from "../types";
import { OwnerLabel } from "./OwnerLabel";

/** A session in the list: title, who/when/how long/mode, and where it stands. */
export function SessionRow({ session: s, onOpen }: { session: SessionSummary; onOpen: (id: string) => void }) {
  const d = duration(s.started_at, s.ended_at);
  return (
    <RowLink
      data-testid="session-row"
      href={`/sessions/${s.id}`}
      onClick={(e) => {
        e.preventDefault();
        onOpen(s.id);
      }}
    >
      <span class="d-item-main">
        <span class={s.title?.trim() ? "ti" : "ti ti--none"}>{titleOf(s)}</span>
        <span class="mt">
          <OwnerLabel owner={s.owner} after=" · " />
          {when(s.started_at)}
          {d && ` · ${d}`}
          {` · ${s.mode}`}
        </span>
      </span>
      <StatusChip status={s.status} streaming={s.streaming} />
    </RowLink>
  );
}
