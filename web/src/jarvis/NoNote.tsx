/** Why the note pane is empty: failed, local only, or simply none. A glyph and words, never colour alone. */
export function NoNote({ reason }: { reason: "failed" | "local" | "none" }) {
  const text =
    reason === "failed"
      ? "This session failed before a note was written."
      : reason === "local"
        ? "Local-only session: no note was generated."
        : "No note was written for this session.";
  return (
    <div class="i-block d-nonote" role="status">
      <span class={`d-nonote-glyph${reason === "failed" ? " d-tone-error" : reason === "none" ? " d-tone-muted" : ""}`} aria-hidden="true">
        {reason === "failed" ? "✕" : reason === "none" ? "—" : <span class="i-sq" />}
      </span>
      <span class={`t-body${reason === "failed" ? " d-tone-error t-strong" : reason === "none" ? " d-tone-muted" : ""}`}>{text}</span>
    </div>
  );
}
