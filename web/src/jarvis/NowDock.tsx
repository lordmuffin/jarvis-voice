import { clock } from "../format";
import type { SuggestionKind } from "../types";
import type { DockItem, DockView } from "./dock";

const KIND: Record<SuggestionKind, string> = {
  question: "Question",
  gap: "Gap",
  counterpoint: "Counterpoint",
  fact_check: "Fact check",
};

const age = (ms: number) => clock(ms);

function Dismiss({ onClick, small }: { onClick: () => void; small?: boolean }) {
  return (
    <button type="button" class="i-x" aria-label="Dismiss suggestion" onClick={onClick} data-small={small ? "" : undefined}>
      ✕
    </button>
  );
}

/**
 * A suggestion in the Now dock. The lead is large (new, ageing or expired); a dismissed one is
 * quiet with an Undo; an older one rides on a single line below (`line`).
 */
export function SuggestionCard({
  item,
  line,
  onDismiss,
  onUndo,
}: {
  item: DockItem;
  line?: boolean;
  onDismiss: (id: string) => void;
  onUndo: (id: string) => void;
}) {
  if (line)
    return (
      <div class="more" data-testid="suggestion" data-state={item.state}>
        <span class="t-tag t-2">{KIND[item.kind]}</span>
        <span class="t-caption t-2 d-grow">{item.text}</span>
        {item.state === "dismissed" ? (
          <>
            <span class="t-meta t-2">Dismissed</span>
            <button type="button" class="i-btn i-btn--quiet i-btn--sm" onClick={() => onUndo(item.id)}>
              ↩ Undo
            </button>
          </>
        ) : (
          <>
            <span class="t-mono t-meta t-2">{item.state === "expired" ? "Expired" : age(item.ageMs)}</span>
            {item.state !== "expired" && <Dismiss onClick={() => onDismiss(item.id)} />}
          </>
        )}
      </div>
    );
  if (item.state === "dismissed")
    return (
      <div data-testid="suggestion" data-state="dismissed" class="d-dock-body">
        <div class="hd">
          <span class="t-label d-label">Now</span>
          <span class="t-meta t-2">Dismissed · {KIND[item.kind]}</span>
          <button type="button" class="i-btn i-btn--quiet i-btn--sm d-end" onClick={() => onUndo(item.id)}>
            ↩ Undo
          </button>
        </div>
        <p class="txt">Nothing else waiting.</p>
      </div>
    );
  return (
    <div data-testid="suggestion" data-state={item.state} class="d-dock-body">
      <div class="hd">
        <span class="t-label d-label">Now</span>
        <span class={item.state === "expired" ? "t-tag" : "t-tag d-tone-accent"}>{KIND[item.kind]}</span>
        {item.state === "new" && <span class="i-new">· New</span>}
        <span class="t-mono t-meta t-2 d-end">{item.state === "expired" ? "Expired" : age(item.ageMs)}</span>
        {item.state !== "expired" && <Dismiss onClick={() => onDismiss(item.id)} />}
      </div>
      <p class="txt">{item.text}</p>
    </div>
  );
}

/**
 * The Now dock: the copilot's one thing to say or ask, at the bottom edge, always the same place
 * and height class. Quiet when there is nothing, when the copilot is offline, or when the
 * session is local only.
 */
export function NowDock({
  view,
  copilotOffline,
  localOnly,
  onDismiss,
  onUndo,
}: {
  view: DockView;
  copilotOffline?: boolean;
  localOnly?: boolean;
  onDismiss: (id: string) => void;
  onUndo: (id: string) => void;
}) {
  const { lead, more } = view;
  const fresh = lead?.state === "new";
  const quiet = !lead || lead.state === "dismissed" || ((copilotOffline || localOnly) && !lead);
  const cls = ["i-dock", quiet && "i-dock--quiet", fresh && "i-fresh"].filter(Boolean).join(" ");
  return (
    <div class={cls} role="region" aria-label="Now">
      {lead ? (
        <SuggestionCard item={lead} onDismiss={onDismiss} onUndo={onUndo} />
      ) : (
        <>
          <div class="hd">
            <span class="t-label d-label">Now</span>
            {localOnly ? (
              <span class="i-chip">
                <span class="i-sq" aria-hidden="true" />
                Local only
              </span>
            ) : (
              copilotOffline && <span class="i-chip i-chip--progress">⚑ Copilot offline</span>
            )}
          </div>
          <p class="txt">
            {localOnly
              ? "The copilot is off in Local only. The server writes the note after you upload."
              : copilotOffline
                ? "The transcript keeps recording. Suggestions return when the copilot is back."
                : "Nothing waiting. Suggestions show up here."}
          </p>
        </>
      )}
      {more.map((m) => (
        <SuggestionCard key={m.id} item={m} line onDismiss={onDismiss} onUndo={onUndo} />
      ))}
    </div>
  );
}
