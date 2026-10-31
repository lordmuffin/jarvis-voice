import { Button, Text } from "../ds/components";
import { duration, titleOf, when } from "../format";
import type { SessionSummary } from "../types";
import { OwnerLabel } from "./OwnerLabel";

/** A session in Recently deleted: title, when, when it goes for good, and Restore. */
export function DeletedRow({
  session: s,
  busy,
  onOpen,
  onRestore,
}: {
  session: SessionSummary;
  busy: boolean;
  onOpen: (id: string) => void;
  onRestore: (s: SessionSummary) => void;
}) {
  const d = duration(s.started_at, s.ended_at);
  return (
    <div data-testid="deleted-row" class="i-row">
      <a
        class="d-item-main"
        href={`/sessions/${s.id}`}
        onClick={(e) => {
          e.preventDefault();
          onOpen(s.id);
        }}
      >
        <span class={s.title?.trim() ? "ti" : "ti ti--none"}>{titleOf(s)}</span>
        <span class="mt">
          <OwnerLabel owner={s.owner} after=" · " />
          {when(s.started_at)}
          {d && ` · ${d}`}
        </span>
        <Text size="caption" tone="muted">
          {s.deleted_at && `Deleted ${when(s.deleted_at)}`}
          {s.trash_expires_at && ` · Removed for good ${when(s.trash_expires_at)}`}
        </Text>
      </a>
      <Button size="sm" disabled={busy} onClick={() => onRestore(s)}>
        Restore
      </Button>
    </div>
  );
}
