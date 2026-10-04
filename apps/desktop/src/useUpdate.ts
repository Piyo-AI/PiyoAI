import { useCallback, useEffect, useState } from "react";

import { inTauri } from "./api";

export type UpdateState =
  | { state: "unavailable" } // browser dev mode: there is no updater
  | { state: "idle" }
  | { state: "checking" }
  | { state: "current" }
  | { state: "available"; version: string; notes: string; flagged: boolean }
  | { state: "installing"; percent: number | null }
  | { state: "restart" }
  | { state: "error"; message: string };

type Pending = import("@tauri-apps/plugin-updater").Update;

/** The version of the running app (null in a plain browser). */
export function useAppVersion(): string | null {
  const [version, setVersion] = useState<string | null>(null);
  useEffect(() => {
    if (!inTauri()) return;
    import("@tauri-apps/api/app").then((app) => app.getVersion()).then(setVersion, () => undefined);
  }, []);
  return version;
}

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
      if (!update) {
        setStatus({ state: "current" });
        return;
      }
      // A version Piyo rolled back from on this computer: the page warns before it is installed again.
      const { invoke } = await import("@tauri-apps/api/core");
      const bad = await invoke<string[]>("bad_versions").catch(() => [] as string[]);
      setStatus({ state: "available", version: update.version, notes: update.body ?? "", flagged: bad.includes(update.version) });
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
      // Records the version being left, so a new version that never starts can be rolled back (rollback.rs).
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("begin_update", { to: pending.version });
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

export interface RollbackProgress {
  state: "idle" | "downloading" | "installing" | "failed";
  version: string;
  percent: number | null;
  error: string | null;
}

/**
 * The shell's rollback of a failed update (rollback.rs): it downloads the previous version by itself, so the app
 * only shows where it stands. `state` is "idle" when nothing is going on.
 */
export function useRollback(): RollbackProgress {
  const [progress, setProgress] = useState<RollbackProgress>({ state: "idle", version: "", percent: null, error: null });
  useEffect(() => {
    if (!inTauri()) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    (async () => {
      const { invoke } = await import("@tauri-apps/api/core");
      const { listen } = await import("@tauri-apps/api/event");
      const stop = await listen<RollbackProgress>("rollback-progress", (e) => setProgress(e.payload));
      if (cancelled) return stop();
      unlisten = stop;
      setProgress(await invoke<RollbackProgress>("rollback_status")); // a rollback that began before this mounted
    })().catch(() => undefined);
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);
  return progress;
}
