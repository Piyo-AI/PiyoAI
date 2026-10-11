import { ReactNode } from "react";

/**
 * The generic response views (core/piyo/views/schema.py): table, key-value, timeline, list, message-list.
 * Display only: no inputs, buttons or images. Every string is drawn as text, and a link is drawn only for an
 * http(s) address, with its host shown so a label cannot hide where it goes.
 */

export interface LinkData {
  text: string;
  url: string;
}
export type CellData = string | number | LinkData | null;
type Tone = "neutral" | "good" | "warn" | "bad";

export interface TableData {
  title?: string | null;
  columns: string[];
  rows: CellData[][];
}
export interface KeyValueData {
  title?: string | null;
  items: { label: string; value: CellData }[];
}
export interface TimelineData {
  title?: string | null;
  items: { when?: string; title: string; detail?: string | null; tone?: Tone; link?: LinkData | null }[];
}
export interface ListData {
  title?: string | null;
  ordered?: boolean;
  items: { text: string; checked?: boolean | null }[];
}
export interface MessageListData {
  title?: string | null;
  items: { sender: string; subject?: string; snippet?: string | null; when?: string; account?: string | null; action?: string }[];
}

// --- shape checks: a stored chat may hold an older shape, so the app checks what it is about to draw ---------

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === "string";
const optStr = (v: unknown) => v === undefined || v === null || isStr(v);
const isLink = (v: unknown): v is LinkData => isObj(v) && isStr(v.text) && isStr(v.url);
const isCell = (v: unknown): v is CellData => v === null || isStr(v) || (typeof v === "number" && Number.isFinite(v)) || isLink(v);
const items = (v: unknown): unknown[] | null => (isObj(v) && Array.isArray(v.items) ? v.items : null);

export function parseTable(d: unknown): TableData | null {
  if (!isObj(d) || !Array.isArray(d.columns) || !d.columns.every(isStr) || !Array.isArray(d.rows) || !optStr(d.title)) return null;
  const width = d.columns.length;
  return d.rows.every((r) => Array.isArray(r) && r.length === width && r.every(isCell)) ? (d as unknown as TableData) : null;
}
export function parseKeyValue(d: unknown): KeyValueData | null {
  const list = items(d);
  return list && optStr((d as Record<string, unknown>).title) && list.every((i) => isObj(i) && isStr(i.label) && isCell(i.value))
    ? (d as unknown as KeyValueData)
    : null;
}
export function parseTimeline(d: unknown): TimelineData | null {
  const list = items(d);
  const ok = (i: unknown) =>
    isObj(i) && isStr(i.title) && optStr(i.when) && optStr(i.detail) && (i.link === undefined || i.link === null || isLink(i.link));
  return list && optStr((d as Record<string, unknown>).title) && list.every(ok) ? (d as unknown as TimelineData) : null;
}
export function parseList(d: unknown): ListData | null {
  const list = items(d);
  const ok = (i: unknown) => isObj(i) && isStr(i.text) && (i.checked === undefined || i.checked === null || typeof i.checked === "boolean");
  return list && optStr((d as Record<string, unknown>).title) && list.every(ok) ? (d as unknown as ListData) : null;
}
export function parseMessageList(d: unknown): MessageListData | null {
  const list = items(d);
  const ok = (i: unknown) => isObj(i) && isStr(i.sender) && optStr(i.subject) && optStr(i.snippet) && optStr(i.when) && optStr(i.account);
  return list && optStr((d as Record<string, unknown>).title) && list.every(ok) ? (d as unknown as MessageListData) : null;
}

// --- drawing --------------------------------------------------------------------------------------------------

/** The URL if it is an http(s) address, else null. */
export function safeUrl(url: string): URL | null {
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u : null;
  } catch {
    return null;
  }
}

function Link({ link }: { link: LinkData }) {
  const u = safeUrl(link.url);
  if (!u) return <>{link.text}</>;
  return (
    <>
      <a href={u.href} target="_blank" rel="noreferrer">
        {link.text}
      </a>
      <span className="v-host"> {u.host}</span>
    </>
  );
}

function Cell({ value }: { value: CellData }): ReactNode {
  if (value === null) return "";
  if (typeof value === "object") return <Link link={value} />;
  return String(value);
}

const Title = ({ text }: { text?: string | null }) => (text ? <div className="v-title">{text}</div> : null);

export function TableView({ data }: { data: TableData }) {
  return (
    <section className="view v-table">
      <Title text={data.title} />
      <div className="v-scroll">
        <table>
          <thead>
            <tr>
              {data.columns.map((c, i) => (
                <th key={i}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} className={typeof cell === "number" ? "num" : undefined}>
                    <Cell value={cell} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function KeyValueView({ data }: { data: KeyValueData }) {
  return (
    <section className="view v-kv">
      <Title text={data.title} />
      <dl>
        {data.items.map((item, i) => (
          <div key={i}>
            <dt>{item.label}</dt>
            <dd>
              <Cell value={item.value} />
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function TimelineView({ data }: { data: TimelineData }) {
  return (
    <section className="view v-timeline">
      <Title text={data.title} />
      <ol>
        {data.items.map((item, i) => (
          <li key={i} className={`tone-${item.tone ?? "neutral"}`}>
            <span className="v-when">{item.when}</span>
            <span className="v-dot" aria-hidden="true" />
            <div>
              <div className="v-item-title">{item.title}</div>
              {item.detail && <div className="v-detail">{item.detail}</div>}
              {item.link && (
                <div className="v-detail">
                  <Link link={item.link} />
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function ListView({ data }: { data: ListData }) {
  const checklist = data.items.some((i) => i.checked !== undefined && i.checked !== null);
  const Tag = data.ordered ? "ol" : "ul";
  return (
    <section className="view v-list">
      <Title text={data.title} />
      <Tag className={checklist ? "checklist" : undefined}>
        {data.items.map((item, i) => (
          <li key={i} className={item.checked ? "done" : undefined}>
            {checklist && (
              <span className="v-box" role="img" aria-label={item.checked ? "done" : "not done"}>
                {item.checked ? "☑" : "☐"}
              </span>
            )}
            {item.text}
          </li>
        ))}
      </Tag>
    </section>
  );
}

const ACTIONS: Record<string, string> = { reply: "Needs a reply", decide: "Needs a decision", read: "To read" };

export function MessageListView({ data }: { data: MessageListData }) {
  return (
    <section className="view v-messages">
      <Title text={data.title} />
      <ul>
        {data.items.map((m, i) => (
          <li key={i}>
            <div className="v-msg-head">
              <strong>{m.sender}</strong>
              {m.account && <span className="v-chip">{m.account}</span>}
              {m.when && <span className="v-when">{m.when}</span>}
            </div>
            {m.subject && <div className="v-item-title">{m.subject}</div>}
            {m.snippet && <div className="v-detail">{m.snippet}</div>}
            {m.action && ACTIONS[m.action] && <span className={`v-chip action-${m.action}`}>{ACTIONS[m.action]}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}
