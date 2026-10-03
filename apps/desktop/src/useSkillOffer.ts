import { useEffect, useState } from "react";

import type { SkillInfo } from "./api";
import type { UiMessage } from "./useChat";

export interface SkillOffer {
  kind: "learn" | "refine";
  /** For refine: the skill that was used in this chat. */
  skill?: string;
}

// What a chat always has; using these says nothing about a procedure worth saving.
const BASELINE = new Set(["load_skill", "current_time", "memory.recall", "memory.remember", "memory.forget"]);
const NEVER = new Set(["schedule.create", "schedule.cancel", "schedule.list", "skill.run_script"]);
const norm = (name: string) => name.replace("__", ".");

/**
 * After a finished request, offers to save the chat as a skill (it took at least two tool calls, one of them real
 * work) or, when the chat used a skill the user can edit, to improve that skill from it.
 */
export function useSkillOffer(
  messages: UiMessage[],
  busy: boolean,
  conversationId: string | null,
  skills: SkillInfo[],
): { offer: SkillOffer | null; dismiss: () => void } {
  const [dismissed, setDismissed] = useState<string | null>(null);

  const last = messages[messages.length - 1];
  const turn: UiMessage[] = [];
  for (let i = messages.length - 1; i >= 0 && messages[i].role !== "user"; i--) turn.unshift(messages[i]);
  const tools = turn.flatMap((m) => m.tools ?? []);
  const key = `${conversationId}:${messages.length}`;

  useEffect(() => {
    setDismissed(null);
  }, [conversationId]);

  const dismiss = () => setDismissed(key); // hides the offer until the next message

  const none = { offer: null, dismiss };
  if (busy || !conversationId || !last || last.role !== "assistant" || last.error || dismissed === key) return none;
  if (tools.some((t) => t.output === undefined)) return none; // something is still running

  const loaded = tools.find((t) => norm(t.name) === "load_skill" && !t.isError)?.arguments?.name;
  const editable = skills.find((s) => s.name === loaded && s.removable);
  if (editable) return { offer: { kind: "refine" as const, skill: editable.name }, dismiss };

  const real = tools.filter((t) => !t.isError && !BASELINE.has(norm(t.name)) && !NEVER.has(norm(t.name)));
  if (real.length >= 1 && tools.length >= 2) return { offer: { kind: "learn" as const }, dismiss };
  return none;
}
