import { ReactNode, useMemo } from "react";

/**
 * Markdown for assistant replies. Everything becomes React elements, never HTML, so a reply (which may repeat text
 * from a web page or an email) cannot inject markup. Only http(s) links are made, images are not supported (a remote
 * image would leak the user's address), and raw `<tags>` stay visible text.
 *
 * Supported: headings, paragraphs (single line breaks kept), nested bullet and numbered lists, block quotes, rules,
 * fenced code, pipe tables, and inline **bold**, *italic*, ~~strike~~, `code`, [links](https://...) and bare URLs.
 * A reply that is still streaming is parsed as it is: an unclosed fence is code until it closes.
 */

export interface ListItem {
  lines: string[];
  children: ListBlock[];
}

export interface ListBlock {
  type: "list";
  ordered: boolean;
  start: number;
  items: ListItem[];
}

export type ChatBlock =
  | { type: "p"; lines: string[] }
  | { type: "heading"; level: number; text: string }
  | { type: "code"; lang: string; text: string }
  | { type: "quote"; blocks: ChatBlock[] }
  | { type: "hr" }
  | { type: "table"; head: string[]; align: ("left" | "center" | "right" | undefined)[]; rows: string[][] }
  | ListBlock;

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)[^`]*$/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^ {0,3}>\s?(.*)$/;
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
const MAX_DEPTH = 6; // nested quotes and inline formatting; deeper text is shown as written

function splitRow(line: string): string[] {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  const cells: string[] = [];
  for (const part of row.split("|")) {
    const prev = cells.length - 1;
    if (prev >= 0 && cells[prev].endsWith("\\")) cells[prev] = cells[prev].slice(0, -1) + "|" + part; // "\|" is a literal bar
    else cells.push(part);
  }
  return cells.map((c) => c.trim());
}

function isTableStart(lines: string[], i: number): boolean {
  return (
    i + 1 < lines.length &&
    lines[i].includes("|") &&
    TABLE_SEPARATOR.test(lines[i + 1]) &&
    lines[i + 1].includes("-") &&
    splitRow(lines[i]).length === splitRow(lines[i + 1]).length
  );
}

function startsBlock(lines: string[], i: number): boolean {
  const line = lines[i];
  return (
    FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || ITEM.test(line) || isTableStart(lines, i)
  );
}

/** Add one list line to the tree of lists; `stack` holds the lists that are open, by indent. */
function addItem(root: ListBlock, stack: { indent: number; list: ListBlock }[], m: RegExpExecArray): void {
  const indent = m[1].replace(/\t/g, "    ").length;
  const ordered = /\d/.test(m[2]);
  while (stack.length > 1 && indent < stack[stack.length - 1].indent) stack.pop();
  let top = stack[stack.length - 1];
  if (indent > top.indent && top.list.items.length) {
    const parent = top.list.items[top.list.items.length - 1];
    const list: ListBlock = { type: "list", ordered, start: ordered ? parseInt(m[2], 10) : 1, items: [] };
    parent.children.push(list);
    stack.push({ indent, list });
    top = stack[stack.length - 1];
  }
  if (top.list === root && root.items.length === 0) {
    root.ordered = ordered;
    root.start = ordered ? parseInt(m[2], 10) : 1;
  }
  top.list.items.push({ lines: [m[3]], children: [] });
}

export function parseChat(markdown: string, depth = 0): ChatBlock[] {
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ChatBlock[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") {
      i++;
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !(lines[i].trim().startsWith(fence[1]) && lines[i].trim().replace(/[`~]/g, "") === "")) {
        body.push(lines[i++]);
      }
      i++; // the closing fence (or the end of a reply that is still arriving)
      blocks.push({ type: "code", lang: fence[2], text: body.join("\n") });
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2] });
      i++;
      continue;
    }

    if (RULE.test(line)) {
      blocks.push({ type: "hr" });
      i++;
      continue;
    }

    if (QUOTE.test(line)) {
      const inner: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i])) inner.push(QUOTE.exec(lines[i++])![1]);
      blocks.push({ type: "quote", blocks: depth < MAX_DEPTH ? parseChat(inner.join("\n"), depth + 1) : [{ type: "p", lines: inner }] });
      continue;
    }

    if (isTableStart(lines, i)) {
      const head = splitRow(lines[i]);
      const align = splitRow(lines[i + 1]).map((c) =>
        c.startsWith(":") && c.endsWith(":") ? ("center" as const) : c.endsWith(":") ? ("right" as const) : c.startsWith(":") ? ("left" as const) : undefined,
      );
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim() !== "" && lines[i].includes("|") && !startsBlock([lines[i]], 0)) {
        const cells = splitRow(lines[i++]);
        rows.push(head.map((_, c) => cells[c] ?? ""));
      }
      blocks.push({ type: "table", head, align, rows });
      continue;
    }

    const first = ITEM.exec(line);
    if (first) {
      const root: ListBlock = { type: "list", ordered: false, start: 1, items: [] };
      const stack = [{ indent: 0, list: root }];
      while (i < lines.length) {
        const m = ITEM.exec(lines[i]);
        if (m) {
          addItem(root, stack, m);
          i++;
        } else if (lines[i].trim() === "") {
          // A blank line inside a list ("loose" list) is fine when the next line is another item.
          let j = i + 1;
          while (j < lines.length && lines[j].trim() === "") j++;
          if (j < lines.length && ITEM.test(lines[j])) i = j;
          else break;
        } else if (/^\s+\S/.test(lines[i]) && !startsBlock(lines, i)) {
          const open = stack[stack.length - 1].list.items;
          open[open.length - 1].lines.push(lines[i].trim()); // a wrapped line of the last item
          i++;
        } else {
          break;
        }
      }
      blocks.push(root);
      continue;
    }

    const para: string[] = [];
    while (i < lines.length && lines[i].trim() !== "" && (para.length === 0 || !startsBlock(lines, i))) para.push(lines[i++].trim());
    blocks.push({ type: "p", lines: para });
  }
  return blocks;
}

const TOKEN =
  /(`+)(.+?)\1(?!`)|\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)|\*\*(\S(?:.*?\S)?)\*\*|~~(\S(?:.*?\S)?)~~|\*(\S(?:[^*\n]*?\S)?)\*|(https?:\/\/[^\s<>]+)/g;
const TRAILING = /[.,;:!?)\]'"]+$/;

/** Inline formatting. Text that matches nothing stays plain text. */
export function renderInline(text: string, depth = 0): ReactNode[] {
  if (depth > 3) return [text];
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(TOKEN)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    last = at + m[0].length;
    const k = key++;
    if (m[2] !== undefined) {
      out.push(<code key={k}>{m[2].trim()}</code>);
    } else if (m[3] !== undefined) {
      out.push(
        <a key={k} href={m[4]} target="_blank" rel="noreferrer">
          {renderInline(m[3], depth + 1)}
        </a>,
      );
    } else if (m[5] !== undefined) {
      out.push(<strong key={k}>{renderInline(m[5], depth + 1)}</strong>);
    } else if (m[6] !== undefined) {
      out.push(<del key={k}>{renderInline(m[6], depth + 1)}</del>);
    } else if (m[7] !== undefined) {
      out.push(<em key={k}>{renderInline(m[7], depth + 1)}</em>);
    } else {
      const url = m[8].replace(TRAILING, "");
      out.push(
        <a key={k} href={url} target="_blank" rel="noreferrer">
          {url}
        </a>,
      );
      last -= m[8].length - url.length; // the trailing punctuation goes back to plain text
    }
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function lines(parts: string[]): ReactNode[] {
  return parts.flatMap((l, i) => (i === 0 ? renderInline(l) : [<br key={`br${i}`} />, ...renderInline(l)]));
}

function renderList(list: ListBlock, key: number): ReactNode {
  const Tag = list.ordered ? "ol" : "ul";
  return (
    <Tag key={key} start={list.ordered && list.start !== 1 ? list.start : undefined}>
      {list.items.map((item, i) => (
        <li key={i}>
          {lines(item.lines)}
          {item.children.map(renderList)}
        </li>
      ))}
    </Tag>
  );
}

function renderBlock(block: ChatBlock, key: number): ReactNode {
  switch (block.type) {
    case "p":
      return <p key={key}>{lines(block.lines)}</p>;
    case "heading": {
      const Tag = `h${Math.min(block.level + 2, 6)}` as "h3" | "h4" | "h5" | "h6";
      return <Tag key={key}>{renderInline(block.text)}</Tag>;
    }
    case "code":
      return (
        <pre key={key} className="md-code">
          <code>{block.text}</code>
        </pre>
      );
    case "quote":
      return <blockquote key={key}>{block.blocks.map(renderBlock)}</blockquote>;
    case "hr":
      return <hr key={key} />;
    case "list":
      return renderList(block, key);
    case "table":
      return (
        <div key={key} className="md-table">
          <table>
            <thead>
              <tr>
                {block.head.map((h, c) => (
                  <th key={c} style={{ textAlign: block.align[c] }}>
                    {renderInline(h)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td key={c} style={{ textAlign: block.align[c] }}>
                      {renderInline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

export function ChatMarkdown({ text }: { text: string }) {
  const blocks = useMemo(() => parseChat(text), [text]);
  return <div className="md">{blocks.map(renderBlock)}</div>;
}
