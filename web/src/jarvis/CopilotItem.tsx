import { clock } from "../format";
import { GLYPH, KIND_LABEL, type Insight } from "../feed";
import type { FinalNoteRef } from "../types";

const TESTID: Record<Insight["kind"], string | undefined> = {
  action: "copilot-action",
  decision: "copilot-decision",
  note: "copilot-note",
  related: "copilot-related",
  work: "feed-work",
};

function Source({ insight }: { insight: Insight }) {
  if (insight.srcMs === undefined) return null;
  return (
    <a class="i-src" href={`#t=${insight.srcMs}`} title={insight.srcText ? `“${insight.srcText}”` : undefined}>
      ▶ {clock(insight.srcMs)}
    </a>
  );
}

/**
 * A copilot finding. In the feed (`event`) it is a row placed where it was found, with the time
 * it arrived; in a grouped list (`item`) it is a compact row. `isNew` tints it and adds the
 * "New" tag, statically.
 */
export function CopilotItem({
  insight,
  variant,
  at,
  isNew,
}: {
  insight: Insight;
  variant: "event" | "item";
  /** Stream time it arrived, for the time column in the feed. */
  at?: number;
  isNew?: boolean;
}) {
  const testid = TESTID[insight.kind];
  const tag = insight.tag && <span class="i-chip">{insight.tag}</span>;
  const body = (
    <>
      {variant === "event" && <span class="g">{GLYPH[insight.kind]}</span>}
      {insight.text}
      {insight.meta && (
        <>
          {" "}
          <span class="m">{insight.meta}</span>
        </>
      )}
      {tag && <> {tag}</>}
      {isNew && (
        <>
          {" "}
          <span class="i-new">New</span>
        </>
      )}
    </>
  );
  if (variant === "item")
    return (
      <div class={`j-item${isNew ? " i-fresh" : ""}`} data-testid={testid}>
        <span class="g" aria-hidden="true">
          {GLYPH[insight.kind]}
        </span>
        <span class="t-body">{body}</span>
        {insight.href ? (
          <a class="t-meta" href={insight.href} aria-label={`Open ${insight.text} in Obsidian`}>
            ▶ Open in Obsidian
          </a>
        ) : (
          <Source insight={insight} />
        )}
      </div>
    );
  return (
    <div class={`i-event${isNew ? " i-fresh" : ""}`} data-testid={testid}>
      <span class="t">{at === undefined ? "" : clock(at)}</span>
      <span class="i-chip k">{KIND_LABEL[insight.kind]}</span>
      <span class="t-body">{body}</span>
      {insight.href ? (
        <a class="t-meta" href={insight.href} aria-label={`Open ${insight.text} in Obsidian`}>
          Obsidian
        </a>
      ) : (
        <Source insight={insight} />
      )}
    </div>
  );
}

/** The note the session ended up with, in the feed once it exists. */
export function FinalNoteRow({ note, at }: { note: FinalNoteRef; at?: number }) {
  return (
    <div class="i-event">
      <span class="t">{at === undefined ? "" : clock(at)}</span>
      <span class="i-chip i-chip--done k">
        <span aria-hidden="true">✓ </span>Note
      </span>
      <span class="t-body t-strong" data-testid="final-note">
        {note.title}
      </span>
      <span />
    </div>
  );
}
