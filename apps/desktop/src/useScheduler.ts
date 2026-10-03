import { useCallback, useEffect, useRef, useState } from "react";

import { api, SchedulerEvent } from "./api";

const POLL_MS = 15000;

/** Unread scheduler notifications (finished runs, held approvals, missed jobs), polled from the core. */
export function useScheduler(onOpenConversation: (id: string) => void) {
  const [events, setEvents] = useState<SchedulerEvent[]>([]);
  const seen = useRef<Set<string> | null>(null); // null until the first poll, so old events do not pop up as desktop notifications

  const poll = useCallback(async () => {
    try {
      const unread = await api.schedulerEvents(true);
      setEvents(unread);
      const known = seen.current;
      seen.current = new Set(unread.map((e) => e.id));
      if (known && typeof Notification !== "undefined" && Notification.permission === "granted") {
        for (const e of unread) {
          if (!known.has(e.id)) {
            try {
              const n = new Notification(e.title, { body: e.body });
              n.onclick = () => e.conversation_id && onOpenConversation(e.conversation_id);
            } catch {
              /* some webviews refuse notifications: the in-app banner still shows */
            }
          }
        }
      }
    } catch {
      /* the core may be restarting; try again next time */
    }
  }, [onOpenConversation]);

  useEffect(() => {
    poll();
    const timer = setInterval(poll, POLL_MS);
    return () => clearInterval(timer);
  }, [poll]);

  const dismiss = async (id: string) => {
    setEvents((list) => list.filter((e) => e.id !== id));
    try {
      await api.markSchedulerEventsRead([id]);
    } catch {
      /* it will show again after the next poll */
    }
  };

  return { events, dismiss, refresh: poll };
}
