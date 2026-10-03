import { useEffect, useState } from "react";

import { api, Provider } from "./api";

/**
 * Things the user should know before sending: local-only mode with a cloud provider, and enabled skills
 * the selected model can't run (vision, context size). Refreshes when the model, its loaded list or the
 * settings window change, since each can change what the core knows.
 */
export function useModelWarnings(provider: Provider | undefined, model: string, refreshOn: unknown[]): string[] {
  const [warnings, setWarnings] = useState<string[]>([]);
  const name = model.trim();

  useEffect(() => {
    if (!provider || !name) {
      setWarnings([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const [settings, skills] = await Promise.all([api.runSettings(), api.skills(provider.id, name)]);
        if (cancelled) return;
        const out: string[] = [];
        if (settings.local_only && !provider.local) {
          out.push(`Local-only mode is on, so ${provider.name} will be refused. Pick a local model or turn it off in Settings > Limits.`);
        }
        for (const s of skills.skills) {
          if (s.enabled && s.model_issues.length) {
            out.push(`The skill "${s.name}" can't run with ${name}: ${s.model_issues.join("; ")}.`);
          }
        }
        setWarnings(out);
      } catch {
        if (!cancelled) setWarnings([]); // the core being unreachable is reported elsewhere
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider?.id, provider?.local, name, ...refreshOn]);

  return warnings;
}
