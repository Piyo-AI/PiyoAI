import { useEffect, useState } from "react";

import { api } from "./api";

export interface SetupNeeded {
  integration: string;
  issue: string;
  skills: string[]; // enabled skills that can't run until it is set up
  guide: string; // the skill whose setup guide to open
}

const dismissKey = (integration: string) => `piyo.setup-dismissed.${integration}`;

function wasDismissed(integration: string): boolean {
  try {
    return window.localStorage.getItem(dismissKey(integration)) === "1";
  } catch {
    return false;
  }
}

/**
 * Integrations that enabled skills need but that are not ready, with a skill whose SETUP.md can fix it.
 * Refreshes when the settings window or the setup wizard closes. The user can dismiss one; that is remembered.
 */
export function useSetupNeeded(refreshOn: unknown[]): { needed: SetupNeeded[]; dismiss: (integration: string) => void } {
  const [needed, setNeeded] = useState<SetupNeeded[]>([]);

  useEffect(() => {
    let cancelled = false;
    api
      .skills()
      .then(({ skills }) => {
        if (cancelled) return;
        const byIntegration = new Map<string, SetupNeeded>();
        for (const s of skills) {
          if (!s.enabled) continue;
          for (const [integration, issue] of Object.entries(s.integration_issues)) {
            const entry = byIntegration.get(integration) ?? { integration, issue, skills: [], guide: "" };
            entry.skills.push(s.name);
            if (s.has_setup && (!entry.guide || s.name === "gmail-triage")) entry.guide = s.name;
            byIntegration.set(integration, entry);
          }
        }
        setNeeded([...byIntegration.values()].filter((n) => n.guide && !wasDismissed(n.integration)));
      })
      .catch(() => !cancelled && setNeeded([])); // the core being unreachable is reported elsewhere
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, refreshOn);

  const dismiss = (integration: string) => {
    try {
      window.localStorage.setItem(dismissKey(integration), "1");
    } catch {
      /* the banner just comes back next time */
    }
    setNeeded((cur) => cur.filter((n) => n.integration !== integration));
  };

  return { needed, dismiss };
}
