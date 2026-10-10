// Minimal Markdown subset for finalizer notes (frontmatter, headings, lists, task items,
// bold, code, wiki-links, http(s) links). Produces a data tree; views render it as elements,
// so nothing is ever injected as HTML.

export type Inline =
  | { t: "text"; v: string }
  | { t: "bold"; v: string }
  | { t: "code"; v: string }
  | { t: "wiki"; v: string }
  | { t: "link"; v: string; href: string };

export type Block =
  | { t: "h"; level: 1 | 2 | 3; inline: Inline[] }
  | { t: "p"; inline: Inline[] }
  | { t: "ul"; items: { checked: boolean | null; inline: Inline[] }[] };

const INLINE = /\*\*(.+?)\*\*|`([^`]+)`|\[\[([^\]]+)\]\]|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;

export function parseInline(src: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const m of src.matchAll(INLINE)) {
    const i = m.index ?? 0;
    if (i > last) out.push({ t: "text", v: src.slice(last, i) });
    if (m[1] !== undefined) out.push({ t: "bold", v: m[1] });
    else if (m[2] !== undefined) out.push({ t: "code", v: m[2] });
    else if (m[3] !== undefined) out.push({ t: "wiki", v: m[3].split("|").pop() ?? m[3] });
    else if (m[4] !== undefined && m[5] !== undefined) out.push({ t: "link", v: m[4], href: m[5] });
    last = i + m[0].length;
  }
  if (last < src.length) out.push({ t: "text", v: src.slice(last) });
  return out;
}

export interface ParseOptions {
  /** Drop a `## <name>` section (and its body), e.g. the transcript the page shows itself. */
  dropSections?: string[];
}

export function parseMarkdown(src: string, opts: ParseOptions = {}): Block[] {
  let text = src.replace(/\r\n?/g, "\n");
  if (text.startsWith("---\n")) {
    const end = text.indexOf("\n---", 4);
    if (end >= 0) text = text.slice(text.indexOf("\n", end + 1) + 1);
  }
  const drop = new Set((opts.dropSections ?? []).map((s) => s.toLowerCase()));
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: { checked: boolean | null; inline: Inline[] }[] | null = null;
  let skipping = false;

  const flush = () => {
    if (para.length) blocks.push({ t: "p", inline: parseInline(para.join(" ")) });
    para = [];
    if (list) blocks.push({ t: "ul", items: list });
    list = null;
  };

  for (const line of text.split("\n")) {
    const h = /^(#{1,3})\s+(.*\S)\s*$/.exec(line);
    if (h) {
      flush();
      const level = h[1]!.length as 1 | 2 | 3;
      skipping = level === 2 && drop.has(h[2]!.toLowerCase());
      if (!skipping) blocks.push({ t: "h", level, inline: parseInline(h[2]!) });
      continue;
    }
    if (skipping) continue;
    const li = /^\s*[-*]\s+(?:\[([ xX])\]\s+)?(.*)$/.exec(line);
    if (li) {
      if (para.length) flush();
      list ??= [];
      list.push({
        checked: li[1] === undefined ? null : li[1] !== " ",
        inline: parseInline(li[2]!),
      });
      continue;
    }
    if (line.trim() === "") {
      flush();
      continue;
    }
    if (list) flush();
    para.push(line.trim());
  }
  flush();
  return blocks;
}
