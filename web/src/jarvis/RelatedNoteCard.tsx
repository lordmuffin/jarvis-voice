import type { Related } from "../types";

/** Obsidian links only; the URI comes from the server. */
const safeUri = (uri: string) => (uri.startsWith("obsidian://") ? uri : undefined);

/** A vault note the copilot thinks is related: title, where it lives, two lines of it. */
export function RelatedNoteCard({ related, isNew }: { related: Related; isNew?: boolean }) {
  const href = safeUri(related.uri);
  return (
    <div class={`j-related${isNew ? " i-fresh" : ""}`} data-testid="related-card">
      <div class="i-row">
        <span class="t-body t-strong">
          {related.title}
          {isNew && (
            <>
              {" "}
              <span class="i-new">New</span>
            </>
          )}
        </span>
        {href && (
          <a class="t-meta" href={href} aria-label={`Open ${related.title} in Obsidian`}>
            ▶ Open in Obsidian
          </a>
        )}
      </div>
      <span class="t-mono t-meta t-2">{related.path}</span>
      <p class="snip">{related.snippet}</p>
    </div>
  );
}
