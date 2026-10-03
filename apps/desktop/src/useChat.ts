import { useCallback, useEffect, useRef, useState } from "react";

import { api, ChatEvent, ChatMessage, ChatSocket, ConversationInfo, StoredMessage } from "./api";

export interface ToolActivity {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  /** undefined while the tool is still running. */
  output?: string;
  isError?: boolean;
}

export interface UiMessage extends ChatMessage {
  error?: boolean;
  tools?: ToolActivity[];
  note?: string;
}

export interface Approval {
  id: string;
  tool: string;
  arguments: Record<string, unknown>;
}

const NOTES: Partial<Record<string, string>> = {
  step_limit: "Stopped: reached the step limit for one request.",
  truncated: "The reply was cut off by the output limit.",
  cancelled: "Stopped.",
};

/** Rebuild what the chat showed from the stored messages: tool results attach to their call. */
export function toUiMessages(stored: StoredMessage[]): UiMessage[] {
  const out: UiMessage[] = [];
  let reply: UiMessage | null = null;
  for (const m of stored) {
    if (m.role === "user") {
      out.push({ role: "user", content: m.content });
      reply = null;
    } else if (m.role === "assistant") {
      if (!reply) {
        reply = { role: "assistant", content: "", tools: [] };
        out.push(reply);
      }
      if (m.content) reply.content += (reply.content ? "\n\n" : "") + m.content;
      for (const c of m.tool_calls) reply.tools!.push({ id: c.id, name: c.name, arguments: c.arguments });
    } else if (reply) {
      const tool = reply.tools!.find((t) => t.id === m.tool_call_id);
      if (tool) {
        tool.output = m.content;
        tool.isError = m.is_error;
      }
    }
  }
  return out;
}

export function useChat() {
  const [conversations, setConversations] = useState<ConversationInfo[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const conversation = useRef<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const history = useRef<UiMessage[]>([]);
  const socket = useRef<ChatSocket | null>(null);
  // True after a tool ran, so the next text starts a new paragraph instead of gluing on.
  const afterTool = useRef(false);
  // Set between pressing Stop and the core confirming (or a timeout), see `stop`.
  const [isStopping, setIsStopping] = useState(false);
  const stopping = useRef(false);
  const stopTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const setConversation = (id: string | null) => {
    conversation.current = id;
    setConversationId(id);
  };

  const refreshList = useCallback(() => {
    api.conversations().then(setConversations).catch(() => undefined);
  }, []);

  const update = (next: UiMessage[]) => {
    history.current = next;
    setMessages(next);
  };

  const patchLast = (change: (m: UiMessage) => UiMessage) => {
    const current = history.current;
    const last = current[current.length - 1];
    if (last?.role === "assistant") update([...current.slice(0, -1), change(last)]);
  };

  const finishStop = () => {
    clearTimeout(stopTimer.current);
    stopping.current = false;
    setIsStopping(false);
    setBusy(false);
  };

  const onEvent = (e: ChatEvent) => {
    // After Stop, drop whatever the old run still sends until the core confirms it has stopped.
    if (stopping.current) {
      if (e.type === "done") finishStop();
      return;
    }
    switch (e.type) {
      case "conversation":
        setConversation(e.id);
        break;
      case "delta": {
        const gap = afterTool.current;
        afterTool.current = false;
        patchLast((m) => ({ ...m, content: m.content + (gap && m.content ? "\n\n" : "") + e.text }));
        break;
      }
      case "tool_start":
        afterTool.current = true;
        patchLast((m) => ({
          ...m,
          tools: [...(m.tools ?? []), { id: e.id, name: e.name, arguments: e.arguments }],
        }));
        break;
      case "tool_end":
        patchLast((m) => ({
          ...m,
          tools: (m.tools ?? []).map((t) => (t.id === e.id ? { ...t, output: e.output, isError: e.is_error } : t)),
        }));
        break;
      case "approval_request":
        setApprovals((a) => [...a, { id: e.id, tool: e.tool, arguments: e.arguments }]);
        break;
      case "done": {
        const note = NOTES[e.reason];
        if (note) patchLast((m) => ({ ...m, note }));
        setApprovals([]);
        setBusy(false);
        refreshList();
        break;
      }
      case "error": {
        // Replace the empty placeholder (or append after partial text) with the error.
        const current = history.current;
        const last = current[current.length - 1];
        const empty = last?.role === "assistant" && last.content === "" && !last.tools?.length;
        const base = empty ? current.slice(0, -1) : current;
        update([...base, { role: "assistant", content: e.message, error: true }]);
        setApprovals([]);
        setBusy(false);
        refreshList();
        break;
      }
    }
  };

  useEffect(() => {
    refreshList();
    const s = new ChatSocket(onEvent);
    socket.current = s;
    return () => s.close();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const send = useCallback(async (text: string, provider: string, model: string) => {
    // The core owns the history: send only the new message and which conversation it belongs to.
    afterTool.current = false;
    update([...history.current, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setBusy(true);
    try {
      await socket.current!.send(provider, model, text, conversation.current);
    } catch (err) {
      update([...history.current.slice(0, -1), { role: "assistant", content: (err as Error).message, error: true }]);
      setBusy(false);
    }
  }, []);

  const respond = useCallback((id: string, approve: boolean) => {
    setApprovals((a) => a.filter((x) => x.id !== id));
    socket.current?.respond(id, approve).catch(() => undefined);
  }, []);

  const stop = useCallback(() => {
    if (stopping.current) return;
    stopping.current = true;
    setIsStopping(true);
    setApprovals([]);
    patchLast((m) => ({ ...m, note: NOTES.cancelled }));
    // The core normally confirms within milliseconds; if it can't (connection lost), don't hang.
    stopTimer.current = setTimeout(finishStop, 3000);
    socket.current?.cancel().catch(finishStop);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const newChat = useCallback(() => {
    update([]);
    setConversation(null);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const open = useCallback(async (id: string) => {
    try {
      const detail = await api.conversation(id);
      update(toUiMessages(detail.messages));
      setConversation(id);
      setApprovals([]);
    } catch (err) {
      update([{ role: "assistant", content: (err as Error).message, error: true }]);
      setConversation(null);
      refreshList();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const remove = useCallback(
    async (id: string) => {
      await api.deleteConversation(id).catch(() => undefined);
      if (conversation.current === id) newChat();
      refreshList();
    },
    [newChat, refreshList],
  );

  return {
    messages,
    busy,
    isStopping,
    approvals,
    conversations,
    conversationId,
    send,
    respond,
    stop,
    newChat,
    open,
    remove,
  };
}
