import { useEffect, useState } from "react";

import type { SkillInfo } from "./api";
import type { UiMessage } from "./useChat";

export interface SkillOffer {
  kind: "learn" | "refine";
  /** For refine: the skill that was used in this chat. */
  skill?: string;
  /** For refine: why now. A failed tool call or a user correction is the moment a skill most needs fixing. */
  reason?: "failed" | "corrected" | "used";
}

// A follow-up that pushes back on what Piyo did. A heuristic: a false hit only shows an offer the user can dismiss.
const CORRECTION =
  /\b(no[,.!]|nope|wrong|incorrect|not what|that's not|that is not|didn't|did not|don't|do not|instead|actually|should (have|not)|shouldn't|you forgot|you missed|too (long|short)|try again|again,)/i;
export const looksLikeCorrection = (text: string) => CORRECTION.test(text);

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

  // The skill may have been loaded in an earlier turn: a correction arrives as the next message.
  const loadedNames = messages
    .flatMap((m) => m.tools ?? [])
    .filter((t) => norm(t.name) === "load_skill" && !t.isError)
    .map((t) => t.arguments?.name);
  const editable = skills.filter((s) => s.removable).find((s) => s.name === loadedNames[loadedNames.length - 1]);
  if (editable) {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    const reason: SkillOffer["reason"] = tools.some((t) => t.isError)
      ? "failed"
      : lastUser && looksLikeCorrection(lastUser.content) && lastUser !== messages[0]
        ? "corrected"
        : "used";
    return { offer: { kind: "refine" as const, skill: editable.name, reason }, dismiss };
  }

  const real = tools.filter((t) => !t.isError && !BASELINE.has(norm(t.name)) && !NEVER.has(norm(t.name)));
  if (real.length >= 1 && tools.length >= 2) return { offer: { kind: "learn" as const }, dismiss };
  return none;
}
