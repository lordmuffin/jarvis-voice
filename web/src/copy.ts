import type { Action } from "./types";

/** A note as read: its Markdown without the YAML frontmatter the vault uses. */
export function noteBody(markdown: string): string {
  const m = /^---\n[\s\S]*?\n---\n+/.exec(markdown);
  return m ? markdown.slice(m[0].length) : markdown;
}

/** Actions as a Markdown checklist, written like the note's own Actions section. */
export function actionsChecklist(actions: Action[]): string {
  return actions
    .map((a) => {
      const extra = [a.owner && `owner: ${a.owner}`, a.due && `due: ${a.due}`].filter(Boolean).join(", ");
      return `- [ ] ${a.text}${extra ? ` (${extra})` : ""}\n`;
    })
    .join("");
}
