//! Keeps Piyo running with the window closed, so scheduled routines still fire.
//!
//! The scheduler lives in the core, which is a child of this process, so all that is needed is for the app not to
//! exit when the window closes. With "keep running" on, closing the window hides it and a tray icon stays; Quit in
//! the tray menu really exits (and stops the core). The choice is kept in `background.json` in the app's config
//! folder. Off by default: nothing stays running unless the user asked for it.
//!
//! Start at login is the autostart plugin (driven from the settings page); it launches the app with
//! `--background`, which starts it hidden in the tray. The settings page only offers it while keep-running is on,
//! because a hidden app with no tray icon could not be reached.

use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use serde::{Deserialize, Serialize};
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, State, Window, WindowEvent};

/// Argument the autostart entry passes so a login start stays in the tray.
pub const BACKGROUND_ARG: &str = "--background";

#[derive(Default, Serialize, Deserialize)]
struct Saved {
    keep_running: bool,
}

#[derive(Default)]
pub struct Background {
    keep_running: AtomicBool,
}

fn file(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join("background.json"))
}

impl Background {
    pub fn load(app: &AppHandle) -> Arc<Self> {
        let saved = file(app)
            .and_then(|p| fs::read_to_string(p).ok())
            .and_then(|s| serde_json::from_str::<Saved>(&s).ok())
            .unwrap_or_default();
        let b = Self::default();
        b.keep_running.store(saved.keep_running, Ordering::SeqCst);
        Arc::new(b)
    }

    pub fn keep_running(&self) -> bool {
        self.keep_running.load(Ordering::SeqCst)
    }
}

/// Brings the main window back (from the tray, or when a second copy of the app was started).
pub fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

/// True when the app was started by the login entry and should stay hidden.
pub fn started_in_background() -> bool {
    std::env::args().any(|a| a == BACKGROUND_ARG)
}

/// Creates the tray icon. Only called when keep-running is on, and again if it is turned on later.
fn ensure_tray(app: &AppHandle) -> Result<(), String> {
    if app.tray_by_id("piyo").is_some() {
        return Ok(());
    }
    let open = MenuItemBuilder::with_id("open", "Open Piyo").build(app).map_err(|e| e.to_string())?;
    let quit = MenuItemBuilder::with_id("quit", "Quit Piyo").build(app).map_err(|e| e.to_string())?;
    let menu = MenuBuilder::new(app).items(&[&open, &quit]).build().map_err(|e| e.to_string())?;
    let mut tray = TrayIconBuilder::with_id("piyo")
        .tooltip("Piyo AI")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => show_main(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                show_main(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app).map(|_| ()).map_err(|e| e.to_string())
}

fn remove_tray(app: &AppHandle) {
    let _ = app.remove_tray_by_id("piyo");
}

/// Sets up the tray when the saved choice asks for it, and hides the window for a login start.
pub fn init(app: &AppHandle, background: &Background) {
    if background.keep_running() {
        if let Err(e) = ensure_tray(app) {
            // No tray (some Linux desktops): never hide the window with nothing to bring it back.
            eprintln!("tray unavailable: {e}");
            background.keep_running.store(false, Ordering::SeqCst);
            return;
        }
        if started_in_background() {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.hide();
            }
        }
    }
}

/// Window close: hide instead of quit while keep-running is on.
pub fn on_window_event(window: &Window, event: &WindowEvent) {
    if let WindowEvent::CloseRequested { api, .. } = event {
        if window.label() != "main" {
            return;
        }
        let background = window.state::<Arc<Background>>();
        if background.keep_running() {
            api.prevent_close();
            let _ = window.hide();
        }
    }
}

#[tauri::command]
pub fn background_status(background: State<'_, Arc<Background>>) -> bool {
    background.keep_running()
}

#[tauri::command]
pub fn set_background(app: AppHandle, background: State<'_, Arc<Background>>, keep_running: bool) -> Result<(), String> {
    if keep_running {
        ensure_tray(&app).map_err(|e| format!("Could not create the tray icon: {e}"))?;
    } else {
        remove_tray(&app);
    }
    background.keep_running.store(keep_running, Ordering::SeqCst);
    if let Some(path) = file(&app) {
        if let Some(dir) = path.parent() {
            let _ = fs::create_dir_all(dir);
        }
        let body = serde_json::to_string(&Saved { keep_running }).map_err(|e| e.to_string())?;
        fs::write(path, body).map_err(|e| format!("Could not save the setting: {e}"))?;
    }
    Ok(())
}
