import { ReactNode } from "react";

/**
 * A deliberately small Markdown subset for setup guides: headings, paragraphs, bullet, numbered and task lists,
 * fenced code, **bold**, `code` and http(s) links. Everything becomes React elements (never HTML), and the only
 * "actions" a guide can ask for are the named widgets below, so a guide can show a button but not run anything.
 */

export interface ListItem {
  text: string;
  task: boolean;
  checked: boolean;
}

export type Block =
  | { type: "p"; text: string }
  | { type: "h3"; text: string }
  | { type: "ul"; items: ListItem[] }
  | { type: "ol"; items: ListItem[] }
  | { type: "code"; text: string }
  | { type: "widget"; name: string; arg: string };

export interface Step {
  title: string;
  blocks: Block[];
}

export interface Guide {
  title: string;
  steps: Step[];
}

const WIDGET = /^:::([a-z][a-z-]*)(?:\s+([a-z0-9-]+))?:::$/;
const TASK = /^\[( |x|X)\]\s+(.*)$/;

/** `## Heading` starts a step; text before the first one becomes an "Overview" step. */
export function parseGuide(markdown: string): Guide {
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  let title = "Setup";
  const steps: Step[] = [];
  let current: Step = { title: "Overview", blocks: [] };
  let para: string[] = [];
  let list: { type: "ul" | "ol"; items: ListItem[] } | null = null;
  let code: string[] | null = null;

  const flushPara = () => {
    if (para.length) current.blocks.push({ type: "p", text: para.join(" ") });
    para = [];
  };
  const flushList = () => {
    if (list) current.blocks.push(list);
    list = null;
  };
  const flush = () => {
    flushPara();
    flushList();
  };
  const endStep = () => {
    flush();
    if (current.blocks.length || steps.length) steps.push(current);
  };

  for (const raw of lines) {
    if (code) {
      if (raw.trim().startsWith("```")) {
        current.blocks.push({ type: "code", text: code.join("\n") });
        code = null;
      } else {
        code.push(raw);
      }
      continue;
    }
    const line = raw.trimEnd();
    if (line.trim().startsWith("```")) {
      flush();
      code = [];
    } else if (/^# /.test(line)) {
      flush();
      title = line.slice(2).trim() || title;
    } else if (/^## /.test(line)) {
      endStep();
      current = { title: line.slice(3).trim(), blocks: [] };
    } else if (/^### /.test(line)) {
      flush();
      current.blocks.push({ type: "h3", text: line.slice(4).trim() });
    } else if (WIDGET.test(line.trim())) {
      flush();
      const m = WIDGET.exec(line.trim())!;
      current.blocks.push({ type: "widget", name: m[1], arg: m[2] ?? "" });
    } else if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      flushPara();
      const ordered = /^\s*\d+\./.test(line);
      const body = line.replace(/^\s*([-*]|\d+\.)\s+/, "");
      const task = TASK.exec(body);
      if (!list || list.type !== (ordered ? "ol" : "ul")) {
        flushList();
        list = { type: ordered ? "ol" : "ul", items: [] };
      }
      list.items.push({
        text: task ? task[2] : body,
        task: !!task,
        checked: !!task && task[1] !== " ",
      });
    } else if (/^\s+\S/.test(line) && list && list.items.length) {
      list.items[list.items.length - 1].text += " " + line.trim(); // a wrapped list line
    } else if (line.trim() === "") {
      flush();
    } else {
      flushList();
      para.push(line.trim());
    }
  }
  if (code) current.blocks.push({ type: "code", text: code.join("\n") });
  endStep();
  return { title, steps: steps.filter((s) => s.blocks.length > 0) };
}

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\))/g;

/** Inline formatting. Links keep only http(s) targets; anything else stays plain text. */
export function renderInline(text: string): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) return <code key={i}>{part.slice(1, -1)}</code>;
    const link = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(part);
    if (link) {
      return (
        <a key={i} href={link[2]} target="_blank" rel="noreferrer">
          {link[1]}
        </a>
      );
    }
    return part;
  });
}
