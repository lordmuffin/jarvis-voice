import { RowLink } from "../ds/components";
import { StatusChip } from "../ds/components";
import { clock, duration, titleOf, when } from "../format";
import { snippetParts } from "../sessionFilters";
import type { SessionSummary } from "../types";
import { OwnerLabel } from "./OwnerLabel";

/** A session in the list: title, who/when/how long/mode, and where it stands. */
export function SessionRow({
  session: s,
  onOpen,
}: {
  session: SessionSummary;
  /** Gets the path to open: the session, or the line a search hit when there is one. */
  onOpen: (path: string) => void;
}) {
  const d = duration(s.started_at, s.ended_at);
  const path = s.match ? `/sessions/${s.id}#t=${s.match.start_ms}` : `/sessions/${s.id}`;
  return (
    <RowLink
      data-testid="session-row"
      href={path}
      onClick={(e) => {
        e.preventDefault();
        onOpen(path);
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
        {s.match && (
          <span class="t-caption t-2 d-wrap" data-testid="session-match">
            “…
            {snippetParts(s.match.snippet).map((p, i) => (p.hit ? <b key={i}>{p.text}</b> : p.text))}
            …” <span class="i-src">▶ {clock(s.match.start_ms)}</span>
          </span>
        )}
      </span>
      <StatusChip status={s.status} streaming={s.streaming} />
    </RowLink>
  );
}
