//! Rolls back a bad update.
//!
//! The updater replaces the installed app in place, so if the new version cannot start there is nothing left to
//! click. The state machine below runs in Rust, before any web UI is needed:
//!
//! 1. Before installing an update the app calls `begin_update`, which records the version it is leaving.
//! 2. Every start of the new version counts as a launch until the app calls `confirm_update_healthy` (it does once
//!    the core is up and the page can talk to it).
//! 3. After `MAX_FAILED_LAUNCHES` starts that never got healthy, the next start offers to go back to the previous
//!    version. If the user agrees, that version is reinstalled through the same updater, so the download is still
//!    checked against Piyo's update key; it comes from that release's own `latest.json`.
//!
//! Debug builds skip all of this.

use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

/// Starts of the updated version that never became healthy before the user is offered the old version.
const MAX_FAILED_LAUNCHES: u32 = 2;
const REPO: &str = "Piyo-AI/PiyoAI";

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct UpdateState {
    /// The version that was running when the update was started.
    pub from: String,
    /// The version that was installed.
    pub to: String,
    /// Starts of `to` that have not been confirmed healthy.
    pub launches: u32,
}

#[derive(Debug, PartialEq)]
pub enum Startup {
    Normal,
    /// The updated version failed to get healthy; offer to return to this version.
    OfferRollback { from: String },
}

/// What to do at start, and the state to store afterwards (None deletes the file).
pub fn on_launch(state: Option<UpdateState>, current: &str) -> (Option<UpdateState>, Startup) {
    let Some(mut state) = state else {
        return (None, Startup::Normal);
    };
    if state.to != current {
        // Another version than the one the update installed (the update did not happen, or this is already the
        // rolled-back version): nothing is waiting for confirmation.
        return (None, Startup::Normal);
    }
    state.launches += 1;
    if state.launches > MAX_FAILED_LAUNCHES {
        let from = state.from.clone();
        // Asked once. Whatever the answer, the next start behaves normally.
        return (None, Startup::OfferRollback { from });
    }
    (Some(state), Startup::Normal)
}

fn state_path(app: &AppHandle) -> Option<PathBuf> {
    let dir = app.path().app_local_data_dir().ok()?;
    fs::create_dir_all(&dir).ok()?;
    Some(dir.join("update-state.json"))
}

fn read(app: &AppHandle) -> Option<UpdateState> {
    serde_json::from_str(&fs::read_to_string(state_path(app)?).ok()?).ok()
}

fn write(app: &AppHandle, state: Option<&UpdateState>) {
    let Some(path) = state_path(app) else { return };
    match state {
        Some(s) => {
            if let Ok(text) = serde_json::to_string(s) {
                let _ = fs::write(path, text);
            }
        }
        None => {
            let _ = fs::remove_file(path);
        }
    }
}

/// Called by the web UI right before it installs an update.
#[tauri::command]
pub fn begin_update(app: AppHandle, to: String) {
    let from = app.package_info().version.to_string();
    write(&app, Some(&UpdateState { from, to, launches: 0 }));
}

/// Called by the web UI once the core is up: the running version works, so there is nothing to roll back.
#[tauri::command]
pub fn confirm_update_healthy(app: AppHandle) {
    write(&app, None);
}

/// Runs at start (release builds). May show a dialog and, if accepted, reinstall the previous version.
pub fn check_on_start(app: AppHandle) {
    if cfg!(debug_assertions) {
        return;
    }
    let current = app.package_info().version.to_string();
    let (next, startup) = on_launch(read(&app), &current);
    write(&app, next.as_ref());
    if let Startup::OfferRollback { from } = startup {
        std::thread::spawn(move || offer(&app, &current, &from));
    }
}

/// Appends a line to `rollback.log` next to the update state, so a rollback that stalls can be diagnosed.
fn log(app: &AppHandle, message: &str) {
    use std::io::Write;
    let Some(path) = state_path(app).map(|p| p.with_file_name("rollback.log")) else { return };
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "{secs} {message}");
    }
}

fn offer(app: &AppHandle, current: &str, from: &str) {
    log(app, &format!("offering rollback {current} -> {from}"));
    let go_back = app
        .dialog()
        .message(format!(
            "Piyo {current} did not start properly after the last update.

Go back to version {from}?              Piyo downloads it, checks it, installs it and restarts."
        ))
        .title("Piyo update problem")
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(
            format!("Go back to {from}"),
            format!("Keep {current}"),
        ))
        .blocking_show();
    log(app, &format!("user chose {}", if go_back { "go back" } else { "keep" }));
    if !go_back {
        return;
    }
    // Run the install on the async runtime, as the update from Settings does (a plain thread stalled here).
    let app = app.clone();
    let from = from.to_string();
    tauri::async_runtime::spawn(async move {
        if let Err(e) = install_previous(&app, &from).await {
            log(&app, &format!("rollback failed: {e}"));
            app.dialog()
                .message(format!(
                    "Could not go back to version {from}: {e}

You can download it from                      https://github.com/{REPO}/releases and install it over this one."
                ))
                .title("Piyo update problem")
                .kind(MessageDialogKind::Error)
                .show(|_| {});
        }
    });
}

/// Reinstalls `version` from its own release. The updater normally refuses an older version; here the only
/// accepted version is the exact one asked for.
async fn install_previous(app: &AppHandle, version: &str) -> Result<(), String> {
    let url = format!("https://github.com/{REPO}/releases/download/v{version}/latest.json");
    let wanted = version.to_string();
    log(app, &format!("checking {url}"));
    let updater = app
        .updater_builder()
        .endpoints(vec![url.parse().map_err(|e| format!("{e}"))?])
        .map_err(|e| e.to_string())?
        .version_comparator(move |_current, remote| remote.version.to_string() == wanted)
        .build()
        .map_err(|e| e.to_string())?;
    let update = updater
        .check()
        .await
        .map_err(|e| e.to_string())?
        .ok_or_else(|| format!("version {version} is not available"))?;
    log(app, &format!("found {}; downloading", update.version));
    let bytes = update.download(|_, _| {}, || {}).await.map_err(|e| e.to_string())?;
    log(app, &format!("downloaded {} bytes; launching the installer (the app exits next)", bytes.len()));
    update.install(bytes).map_err(|e| e.to_string())?;
    // On Windows `install` exits the app; elsewhere the new version needs a restart.
    log(app, "installed; restarting");
    app.restart()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state(launches: u32) -> UpdateState {
        UpdateState { from: "0.1.0".into(), to: "0.1.1".into(), launches }
    }

    #[test]
    fn no_state_means_a_normal_start() {
        assert_eq!(on_launch(None, "0.1.0"), (None, Startup::Normal));
    }

    #[test]
    fn first_starts_of_the_new_version_are_counted() {
        assert_eq!(on_launch(Some(state(0)), "0.1.1"), (Some(state(1)), Startup::Normal));
        assert_eq!(on_launch(Some(state(1)), "0.1.1"), (Some(state(2)), Startup::Normal));
    }

    #[test]
    fn third_unconfirmed_start_offers_the_old_version_once() {
        let (next, startup) = on_launch(Some(state(2)), "0.1.1");
        assert_eq!(startup, Startup::OfferRollback { from: "0.1.0".into() });
        assert_eq!(next, None);
    }

    #[test]
    fn a_different_running_version_clears_the_state() {
        // The update never installed, or this already is the old version again.
        assert_eq!(on_launch(Some(state(2)), "0.1.0"), (None, Startup::Normal));
    }
}
