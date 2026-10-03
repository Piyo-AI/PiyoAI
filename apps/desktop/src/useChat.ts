import { useCallback, useEffect, useRef, useState } from "react";

import { ChatMessage, ChatSocket } from "./api";

export interface UiMessage extends ChatMessage {
  error?: boolean;
}

export function useChat() {
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const history = useRef<UiMessage[]>([]);
  const socket = useRef<ChatSocket | null>(null);

  const update = (next: UiMessage[]) => {
    history.current = next;
    setMessages(next);
  };

  useEffect(() => {
    const s = new ChatSocket((e) => {
      const current = history.current;
      const last = current[current.length - 1];
      if (e.type === "delta" && last?.role === "assistant") {
        update([...current.slice(0, -1), { ...last, content: last.content + e.text }]);
      } else if (e.type === "done") {
        setBusy(false);
      } else if (e.type === "error") {
        // Replace the empty placeholder (or append after partial text) with the error.
        const base = last?.role === "assistant" && last.content === "" ? current.slice(0, -1) : current;
        update([...base, { role: "assistant", content: e.message, error: true }]);
        setBusy(false);
      }
    });
    socket.current = s;
    return () => s.close();
  }, []);

  const send = useCallback(async (text: string, provider: string, model: string) => {
    const wire = [...history.current.filter((m) => !m.error), { role: "user", content: text } as UiMessage];
    update([...wire, { role: "assistant", content: "" }]);
    setBusy(true);
    try {
      await socket.current!.send(provider, model, wire);
    } catch (err) {
      update([...wire, { role: "assistant", content: (err as Error).message, error: true }]);
      setBusy(false);
    }
  }, []);

  const clear = useCallback(() => update([]), []);

  return { messages, busy, send, clear };
}
