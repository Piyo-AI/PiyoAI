import { useCallback, useEffect, useState } from "react";

import { api, McpServer } from "./api";

/** The MCP servers the user added, and the actions on them; every change reloads the list from the core. */
export function useMcpServers() {
  const [servers, setServers] = useState<McpServer[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // the server being checked, which can take a while

  const reload = useCallback(async () => {
    try {
      setServers(await api.mcpServers());
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  /** Runs a change, shows its error if it fails, and reports whether it worked. */
  const attempt = async (fn: () => Promise<unknown>, server?: string): Promise<boolean> => {
    setError(null);
    setBusy(server ?? null);
    try {
      await fn();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(null);
      await reload();
    }
  };

  return { servers, error, busy, attempt };
}

/** "KEY=value" lines to an object; lines without "=" are left out. */
export function parseEnv(text: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const i = line.indexOf("=");
    if (i > 0) env[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return env;
}

export const lines = (text: string) =>
  text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
