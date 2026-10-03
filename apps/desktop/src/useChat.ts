import { useCallback, useEffect, useRef, useState } from "react";

import { ChatEvent, ChatMessage, ChatSocket } from "./api";

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

export function useChat() {
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
        break;
      }
    }
  };

  useEffect(() => {
    const s = new ChatSocket(onEvent);
    socket.current = s;
    return () => s.close();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const send = useCallback(async (text: string, provider: string, model: string) => {
    // Only the text goes back to the core; tool activity is display-only.
    const wire: ChatMessage[] = [
      ...history.current.filter((m) => !m.error).map(({ role, content }) => ({ role, content })),
      { role: "user", content: text },
    ];
    afterTool.current = false;
    update([...history.current, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setBusy(true);
    try {
      await socket.current!.send(provider, model, wire);
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

  const clear = useCallback(() => update([]), []);

  return { messages, busy, isStopping, approvals, send, respond, stop, clear };
}
