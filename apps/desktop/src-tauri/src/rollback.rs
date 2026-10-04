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
//! The rollback reports its progress (`rollback-progress` event, `rollback_status` command) so the app can show the
//! download. A version that was rolled back from is remembered (`bad-versions.json`) so the update page can warn
//! before it is installed again; running it successfully later clears the entry.
//!
//! Debug builds skip all of this.

use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};
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

/// Where a rollback stands, for the Updates page.
#[derive(Serialize, Clone, Default, Debug)]
pub struct Progress {
    /// "idle", "downloading", "installing" or "failed".
    pub state: String,
    pub version: String,
    pub percent: Option<u8>,
    pub error: Option<String>,
}

#[derive(Default)]
pub struct RollbackProgress(Mutex<Progress>);

fn report(app: &AppHandle, progress: Progress) {
    if let Some(state) = app.try_state::<RollbackProgress>() {
        *state.0.lock().unwrap() = progress.clone();
    }
    let _ = app.emit("rollback-progress", progress);
}

#[tauri::command]
pub fn rollback_status(state: State<RollbackProgress>) -> Progress {
    let mut p = state.0.lock().unwrap().clone();
    if p.state.is_empty() {
        p.state = "idle".into();
    }
    p
}

/// `list` with `version` added once.
pub fn with_version(mut list: Vec<String>, version: &str) -> Vec<String> {
    if !list.iter().any(|v| v == version) {
        list.push(version.to_string());
    }
    list
}

/// `list` without `version`.
pub fn without_version(list: Vec<String>, version: &str) -> Vec<String> {
    list.into_iter().filter(|v| v != version).collect()
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

fn bad_path(app: &AppHandle) -> Option<PathBuf> {
    Some(state_path(app)?.with_file_name("bad-versions.json"))
}

fn read_bad(app: &AppHandle) -> Vec<String> {
    bad_path(app)
        .and_then(|p| fs::read_to_string(p).ok())
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default()
}

fn write_bad(app: &AppHandle, list: &[String]) {
    if let (Some(path), Ok(text)) = (bad_path(app), serde_json::to_string(list)) {
        let _ = fs::write(path, text);
    }
}

/// Versions that failed to start on this computer and were rolled back from.
#[tauri::command]
pub fn bad_versions(app: AppHandle) -> Vec<String> {
    read_bad(&app)
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
    let current = app.package_info().version.to_string();
    let bad = read_bad(&app);
    if bad.iter().any(|v| *v == current) {
        write_bad(&app, &without_version(bad, &current)); // it works after all
    }
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
    write_bad(app, &with_version(read_bad(app), current));
    // Run the install on the async runtime, as the update from Settings does (a plain thread stalled here).
    let app = app.clone();
    let from = from.to_string();
    tauri::async_runtime::spawn(async move {
        report(&app, Progress { state: "downloading".into(), version: from.clone(), ..Default::default() });
        if let Err(e) = install_previous(&app, &from).await {
            log(&app, &format!("rollback failed: {e}"));
            report(
                &app,
                Progress { state: "failed".into(), version: from.clone(), error: Some(e.clone()), ..Default::default() },
            );
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
    let mut done: u64 = 0;
    let mut last: Option<u8> = None;
    let progress_app = app.clone();
    let target = version.to_string();
    let bytes = update
        .download(
            move |chunk, total| {
                done += chunk as u64;
                let percent = total.filter(|t| *t > 0).map(|t| ((done * 100) / t).min(100) as u8);
                if percent != last {
                    last = percent;
                    report(
                        &progress_app,
                        Progress { state: "downloading".into(), version: target.clone(), percent, error: None },
                    );
                }
            },
            || {},
        )
        .await
        .map_err(|e| e.to_string())?;
    report(app, Progress { state: "installing".into(), version: version.to_string(), percent: Some(100), error: None });
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

    #[test]
    fn bad_versions_are_added_once_and_can_be_cleared() {
        let list = with_version(with_version(vec![], "0.1.2"), "0.1.2");
        assert_eq!(list, vec!["0.1.2".to_string()]);
        assert_eq!(without_version(list, "0.1.2"), Vec::<String>::new());
    }
}
