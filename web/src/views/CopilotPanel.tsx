import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";
import type { Copilot, FinalNoteRef, SuggestionKind } from "../types";

const KIND: Record<SuggestionKind, string> = {
  question: "Question",
  gap: "Gap",
  counterpoint: "Counterpoint",
  fact_check: "Fact check",
};

function Section({ title, children }: { title: string; children: ComponentChildren }) {
  return (
    <section class="cp-section">
      <h3 class="label">{title}</h3>
      {children}
    </section>
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
    <div class="copilot" data-testid="copilot">
      {empty && <p class="muted">Nothing yet.</p>}

      {suggestions.length > 0 && (
        <Section title="Suggestions">
          {suggestions.map((s) => (
            <div class="suggestion" key={s.id} data-testid="suggestion">
              <div>
                <div class="kind">{KIND[s.kind]}</div>
                <div>{s.text}</div>
              </div>
              <button
                class="btn btn-quiet"
                aria-label="Dismiss suggestion"
                onClick={() => setDismissed(new Set(dismissed).add(s.id))}
              >
                ✕
              </button>
            </div>
          ))}
        </Section>
      )}

      {copilot.notes.length > 0 && (
        <Section title="Notes">
          <ul class="bullets">
            {copilot.notes.map((n) => (
              <li key={n.id} data-testid="copilot-note">
                {n.text}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {copilot.actions.length > 0 && (
        <Section title="Actions">
          <ul class="bullets">
            {copilot.actions.map((a) => (
              <li key={a.id} data-testid="copilot-action">
                {a.text}
                {(a.owner || a.due) && (
                  <div class="meta">{[a.owner, a.due].filter(Boolean).join(" · ")}</div>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {copilot.decisions.length > 0 && (
        <Section title="Decisions">
          <ul class="bullets">
            {copilot.decisions.map((d) => (
              <li key={d.id} data-testid="copilot-decision">
                {d.text}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {copilot.related.length > 0 && (
        <Section title="Related">
          {copilot.related.map((r) => {
            const href = safeUri(r.uri);
            const body = (
              <>
                <span class="accent">{r.title}</span>
                <span class="meta clamp">{r.snippet}</span>
              </>
            );
            return href ? (
              <a class="related" key={r.path} href={href}>
                {body}
              </a>
            ) : (
              <div class="related" key={r.path}>
                {body}
              </div>
            );
          })}
        </Section>
      )}

      {finalNote && (
        <Section title="Final note">
          <span class="accent" data-testid="final-note">
            {finalNote.title}
          </span>
        </Section>
      )}
    </div>
  );
}
