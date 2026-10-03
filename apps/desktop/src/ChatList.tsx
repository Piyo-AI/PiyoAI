import { useEffect, useState } from "react";

import { ConversationInfo } from "./api";

interface Props {
  conversations: ConversationInfo[];
  currentId: string | null;
  busy: boolean;
  hasMore: boolean;
  loading: boolean;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  onMore: () => void;
}

const DAY = 24 * 60 * 60 * 1000;

/** "Today", "Yesterday", ... by local calendar day, so the groups match what the user sees on their clock. */
function groupOf(iso: string, now = new Date()): string {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = new Date(iso).getTime();
  if (t >= startOfToday) return "Today";
  if (t >= startOfToday - DAY) return "Yesterday";
  if (t >= startOfToday - 7 * DAY) return "Previous 7 days";
  if (t >= startOfToday - 30 * DAY) return "Previous 30 days";
  return "Older";
}

function grouped(conversations: ConversationInfo[]): [string, ConversationInfo[]][] {
  const groups: [string, ConversationInfo[]][] = [];
  for (const c of conversations) {
    const name = groupOf(c.updated_at);
    const last = groups[groups.length - 1];
    if (last && last[0] === name) last[1].push(c);
    else groups.push([name, [c]]);
  }
  return groups;
}

function TrashIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M6 6l1 14h10l1-14" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  );
}

/** The past-chats list: grouped by day, delete tucked into the row, confirmation in place. */
export function ChatList({ conversations, currentId, busy, hasMore, loading, onOpen, onDelete, onMore }: Props) {
  const [confirming, setConfirming] = useState<string | null>(null);

  useEffect(() => {
    if (!confirming) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setConfirming(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirming]);

  if (conversations.length === 0) {
    return <p className="chat-empty">{loading ? "Loading…" : "Your chats will appear here."}</p>;
  }

  return (
    <nav className="chat-list" aria-label="Past chats">
      {grouped(conversations).map(([name, items]) => (
        <section key={name}>
          <h4 className="chat-group">{name}</h4>
          {items.map((c) =>
            confirming === c.id ? (
              <div key={c.id} className="chat-row confirm" role="alertdialog" aria-label={`Delete ${c.title}?`}>
                <span className="chat-confirm-text">Delete this chat?</span>
                <button
                  className="chat-confirm-yes"
                  autoFocus
                  onClick={() => {
                    setConfirming(null);
                    onDelete(c.id);
                  }}
                >
                  Delete
                </button>
                <button className="chat-confirm-no" onClick={() => setConfirming(null)}>
                  Cancel
                </button>
              </div>
            ) : (
              <div key={c.id} className={`chat-row${c.id === currentId ? " current" : ""}`}>
                <button className="chat-open" onClick={() => onOpen(c.id)} disabled={busy} title={c.title}>
                  {c.title}
                </button>
                <button
                  className="chat-delete"
                  aria-label={`Delete ${c.title}`}
                  title="Delete chat"
                  disabled={busy}
                  onClick={() => setConfirming(c.id)}
                >
                  <TrashIcon />
                </button>
              </div>
            ),
          )}
        </section>
      ))}
      {hasMore && (
        <button className="chat-more" onClick={onMore} disabled={loading}>
          {loading ? "Loading…" : "Show more"}
        </button>
      )}
    </nav>
  );
}
