import { useCallback, useEffect, useState } from "react";

import { api, BrowserInstall } from "./api";

const POLL_MS = 1000;

/** The one-time download of Piyo's browser. It only starts when the user presses the button. */
export function useBrowserInstall(refreshKey: unknown) {
  const [status, setStatus] = useState<BrowserInstall | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    api.browserInstall().then(setStatus, () => undefined);
  }, []);

  useEffect(refresh, [refresh, refreshKey]);
  useEffect(() => {
    if (status?.state !== "installing") return;
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [status?.state, refresh]);

  const install = useCallback(async () => {
    setError("");
    try {
      setStatus(await api.installBrowser());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the download.");
    }
  }, []);

  return { status, error, install };
}
