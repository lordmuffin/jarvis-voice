import { parseMarkdown, type Inline } from "../markdown";

function Inlines({ items }: { items: Inline[] }) {
  return (
    <>
      {items.map((it, i) => {
        switch (it.t) {
          case "bold":
            return <strong key={i}>{it.v}</strong>;
          case "code":
            return <code key={i}>{it.v}</code>;
          case "wiki":
            return (
              <span class="wiki" key={i}>
                {it.v}
              </span>
            );
          case "link":
            return (
              <a key={i} href={it.href} target="_blank" rel="noopener noreferrer">
                {it.v}
              </a>
            );
          default:
            return it.v;
        }
      })}
    </>
  );
}

/** Renders the finalizer's Markdown note. The transcript section is shown by its own pane. */
export function NoteView({ markdown }: { markdown: string }) {
  const blocks = parseMarkdown(markdown, { dropSections: ["Transcript"] });
  return (
    <article class="note" data-testid="note">
      {blocks.map((b, i) => {
        if (b.t === "h") {
          const H = (`h${b.level + 1}` as "h2" | "h3" | "h4");
          return (
            <H key={i}>
              <Inlines items={b.inline} />
            </H>
          );
        }
        if (b.t === "ul") {
          return (
            <ul key={i} class="bullets">
              {b.items.map((li, j) => (
                <li key={j}>
                  {li.checked !== null && (
                    <span aria-hidden="true">{li.checked ? "☑ " : "☐ "}</span>
                  )}
                  <Inlines items={li.inline} />
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={i}>
            <Inlines items={b.inline} />
          </p>
        );
      })}
    </article>
  );
}
