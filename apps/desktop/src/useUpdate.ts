import { useCallback, useEffect, useState } from "react";

import { inTauri } from "./api";

export type UpdateState =
  | { state: "unavailable" } // browser dev mode: there is no updater
  | { state: "idle" }
  | { state: "checking" }
  | { state: "current" }
  | { state: "available"; version: string; notes: string }
  | { state: "installing"; percent: number | null }
  | { state: "restart" }
  | { state: "error"; message: string };

type Pending = import("@tauri-apps/plugin-updater").Update;

/**
 * Checks for a new version and installs it. The updater only accepts a download whose signature matches the
 * public key in tauri.conf.json, so a tampered or wrongly signed file is refused before anything is installed.
 * Nothing is installed without the user pressing Install. `auto` checks once when the hook mounts.
 */
export function useUpdate(auto = false) {
  const [status, setStatus] = useState<UpdateState>({ state: inTauri() ? "idle" : "unavailable" });
  const [pending, setPending] = useState<Pending | null>(null);

  const check = useCallback(async () => {
    if (!inTauri()) return;
    setStatus({ state: "checking" });
    try {
      const { check: checkForUpdate } = await import("@tauri-apps/plugin-updater");
      const update = await checkForUpdate();
      setPending(update);
      setStatus(update ? { state: "available", version: update.version, notes: update.body ?? "" } : { state: "current" });
    } catch (e) {
      setStatus({ state: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, []);

  const install = useCallback(async () => {
    if (!pending) return;
    let total = 0;
    let done = 0;
    setStatus({ state: "installing", percent: null });
    try {
      await pending.downloadAndInstall((event) => {
        if (event.event === "Started") total = event.data.contentLength ?? 0;
        if (event.event === "Progress") {
          done += event.data.chunkLength;
          setStatus({ state: "installing", percent: total ? Math.min(100, Math.round((done / total) * 100)) : null });
        }
      });
      setStatus({ state: "restart" });
    } catch (e) {
      setStatus({ state: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, [pending]);

  const restart = useCallback(async () => {
    const { relaunch } = await import("@tauri-apps/plugin-process");
    await relaunch();
  }, []);

  useEffect(() => {
    if (auto) void check();
  }, [auto, check]);

  return { status, check, install, restart };
}
