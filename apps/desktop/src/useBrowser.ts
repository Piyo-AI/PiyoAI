import { useCallback, useEffect, useState } from "react";

import { api, BrowserStatus } from "./api";

const POLL_MS = 5000;

/** Piyo's browser: whether it runs, whether its window is shown, and the controls for both. */
export function useBrowser(refreshKey: unknown) {
  const [status, setStatus] = useState<BrowserStatus | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    api.browser().then(setStatus, () => undefined);
  }, []);

  useEffect(refresh, [refresh, refreshKey]);
  useEffect(() => {
    if (!status?.running) return;
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [status?.running, refresh]);

  const setVisible = useCallback(async (visible: boolean) => {
    setError("");
    try {
      setStatus(await api.setBrowserVisible(visible));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not change the browser.");
    }
  }, []);

  const closePages = useCallback(async () => {
    setError("");
    try {
      setStatus(await api.stopBrowser());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not close the pages.");
    }
  }, []);

  return { status, error, setVisible, closePages };
}
