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

/** Where a note stands on its way to the vault repo. State colors always carry a word. */
export function PublicationChip({ publication }: { publication: Publication | null }) {
  const state = publication?.state;
  return (
    <span class={`chip chip-pub-${state ?? "none"}`} data-testid="publication-chip">
      {state && GLYPH[state] && <span aria-hidden="true">{GLYPH[state]}</span>}
      {state ? LABEL[state] : "Not published"}
    </span>
  );
}
