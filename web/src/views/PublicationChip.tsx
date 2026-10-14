import { Pill, type PillTone } from "../ds/components";
import type { Publication, PublicationState } from "../types";

const LABEL: Record<PublicationState, string> = {
  publishing: "Publishing",
  open: "PR open",
  merged: "Merged",
  closed: "PR closed",
  skipped: "Skipped",
  failed: "Publish failed",
};

const GLYPH: Partial<Record<PublicationState, string>> = { merged: "✓ ", failed: "✕ " };

const TONE: Record<PublicationState, PillTone> = {
  publishing: "progress",
  open: "live",
  merged: "done",
  closed: "neutral",
  skipped: "neutral",
  failed: "failed",
};

/** Where a note stands on its way to the vault repo. State colors always carry a word. */
export function PublicationChip({ publication }: { publication: Publication | null }) {
  const state = publication?.state;
  return (
    <Pill tone={state ? TONE[state] : "neutral"} data-testid="publication-chip">
      {state && GLYPH[state] && <span aria-hidden="true">{GLYPH[state]}</span>}
      {state ? LABEL[state] : "Not published"}
    </Pill>
  );
}
