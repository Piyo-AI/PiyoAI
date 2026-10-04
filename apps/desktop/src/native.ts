import { inTauri } from "./api";

/** Desktop-only features (system notifications, tray, start at login). Everything here is a no-op in a plain browser. */

/** Shows a system notification; falls back to the webview's own when not running in the Tauri shell. */
export async function notify(title: string, body: string): Promise<void> {
  if (inTauri()) {
    const { isPermissionGranted, sendNotification } = await import("@tauri-apps/plugin-notification");
    if (await isPermissionGranted()) sendNotification({ title, body });
    return;
  }
  if (typeof Notification !== "undefined" && Notification.permission === "granted") new Notification(title, { body });
}

/** Asks the OS for permission to notify, once (the system remembers the answer). */
export async function requestNotifyPermission(): Promise<void> {
  if (inTauri()) {
    const { isPermissionGranted, requestPermission } = await import("@tauri-apps/plugin-notification");
    if (!(await isPermissionGranted())) await requestPermission();
    return;
  }
  if (typeof Notification !== "undefined" && Notification.permission === "default") await Notification.requestPermission();
}

export interface BackgroundState {
  keepRunning: boolean;
  startAtLogin: boolean;
}

/** Null outside the desktop app, where there is no tray to keep Piyo in. */
export async function getBackground(): Promise<BackgroundState | null> {
  if (!inTauri()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  const { isEnabled } = await import("@tauri-apps/plugin-autostart");
  return { keepRunning: await invoke<boolean>("background_status"), startAtLogin: await isEnabled() };
}

/** Turning keep-running off also turns start at login off: a hidden app with no tray icon could not be reached. */
export async function setKeepRunning(on: boolean): Promise<void> {
  const { invoke } = await import("@tauri-apps/api/core");
  if (!on) await setStartAtLogin(false);
  await invoke("set_background", { keepRunning: on });
}

export async function setStartAtLogin(on: boolean): Promise<void> {
  const { enable, disable } = await import("@tauri-apps/plugin-autostart");
  await (on ? enable() : disable());
}
