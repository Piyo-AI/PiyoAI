import { useSyncExternalStore } from "react";

/**
 * Whether Piyo may suggest saving or improving a skill after a chat. "Never" is kept on this computer
 * (localStorage; Settings > Skills turns it back on); "this session" lasts until the app is restarted.
 */
const KEY = "piyo.skillSuggestions";

function readOff(): boolean {
  try {
    return localStorage.getItem(KEY) === "off";
  } catch {
    return false; // storage can be blocked: suggestions then stay on
  }
}

let off = readOff();
let sessionHidden = false;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function hideSuggestionsThisSession(): void {
  sessionHidden = true;
  emit();
}

export function setSuggestionsEnabled(on: boolean): void {
  off = !on;
  if (on) sessionHidden = false;
  try {
    if (on) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, "off");
  } catch {
    /* kept in memory for this session only */
  }
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}

/** `enabled` is the saved choice (the Settings checkbox); `visible` also counts "hide for this session". */
export function useSkillSuggestions() {
  const enabled = useSyncExternalStore(subscribe, () => !off);
  const hidden = useSyncExternalStore(subscribe, () => sessionHidden);
  return { enabled, visible: enabled && !hidden };
}
