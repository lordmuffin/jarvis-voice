import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";
import {
  Button,
  Bullets,
  EmptyState,
  Item,
  Related,
  Section,
  SectionLabel,
  Stack,
  Text,
} from "../ds/components";
import type { Copilot, FinalNoteRef, SuggestionKind } from "../types";

const KIND: Record<SuggestionKind, string> = {
  question: "Question",
  gap: "Gap",
  counterpoint: "Counterpoint",
  fact_check: "Fact check",
};

function CopilotSection({ title, children }: { title: string; children: ComponentChildren }) {
  return (
    <Stack>
      <SectionLabel as="h3">{title}</SectionLabel>
      {children}
    </Stack>
  );
}

/** Obsidian links only; the URI comes from the server. */
const safeUri = (uri: string) => (uri.startsWith("obsidian://") ? uri : undefined);

/**
 * The same panes as the Mac CopilotPanel. `streamMs` is the live stream clock used to hide
 * expired suggestions; pass `null` for a finished session (suggestions are then omitted).
 */
export function CopilotPanel({
  copilot,
  streamMs,
  finalNote,
}: {
  copilot: Copilot;
  streamMs: number | null;
  finalNote: FinalNoteRef | null;
}) {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const suggestions =
    streamMs === null
      ? []
      : copilot.suggestions.filter((s) => !dismissed.has(s.id) && s.expires_at_ms > streamMs);
  const empty =
    !suggestions.length &&
    !copilot.notes.length &&
    !copilot.actions.length &&
    !copilot.decisions.length &&
    !copilot.related.length &&
    !finalNote;

  return (
    <Section data-testid="copilot">
      {empty && <EmptyState>Nothing yet.</EmptyState>}

      {suggestions.length > 0 && (
        <CopilotSection title="Suggestions">
          {suggestions.map((s) => (
            <Item key={s.id} data-testid="suggestion">
              <div>
                <Text size="label" tone="warning">
                  {KIND[s.kind]}
                </Text>
                <div>{s.text}</div>
              </div>
              <Button
                variant="quiet"
                size="sm"
                aria-label="Dismiss suggestion"
                onClick={() => setDismissed(new Set(dismissed).add(s.id))}
              >
                ✕
              </Button>
            </Item>
          ))}
        </CopilotSection>
      )}

      {copilot.notes.length > 0 && (
        <CopilotSection title="Notes">
          <Bullets>
            {copilot.notes.map((n) => (
              <li key={n.id} data-testid="copilot-note">
                {n.text}
              </li>
            ))}
          </Bullets>
        </CopilotSection>
      )}

      {copilot.actions.length > 0 && (
        <CopilotSection title="Actions">
          <Bullets>
            {copilot.actions.map((a) => (
              <li key={a.id} data-testid="copilot-action">
                {a.text}
                {(a.owner || a.due) && (
                  <Text as="div" size="meta" tone="muted">
                    {[a.owner, a.due].filter(Boolean).join(" · ")}
                  </Text>
                )}
              </li>
            ))}
          </Bullets>
        </CopilotSection>
      )}

      {copilot.decisions.length > 0 && (
        <CopilotSection title="Decisions">
          <Bullets>
            {copilot.decisions.map((d) => (
              <li key={d.id} data-testid="copilot-decision">
                {d.text}
              </li>
            ))}
          </Bullets>
        </CopilotSection>
      )}

      {copilot.related.length > 0 && (
        <CopilotSection title="Related">
          {copilot.related.map((r) => (
            <Related key={r.path} href={safeUri(r.uri)} title={r.title} snippet={r.snippet} />
          ))}
        </CopilotSection>
      )}

      {finalNote && (
        <CopilotSection title="Final note">
          <Text tone="accent" data-testid="final-note">
            {finalNote.title}
          </Text>
        </CopilotSection>
      )}
    </Section>
  );
}
