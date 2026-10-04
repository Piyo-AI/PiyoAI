//! Runs the Python core as a child process and tells the webview how to reach it.
//!
//! The core prints one JSON line `{"port","token"}` on stdout when it is ready. The webview polls
//! `core_status` until it is `ready`, so nothing here blocks the main thread. If the core dies on its own the
//! status becomes `failed` and a `core-exited` event is emitted; `restart_core` starts it again. Debug builds run
//! the core from the source tree; release builds run the packaged core from the app's resources.
//!
//! The core also exits by itself when its stdin closes (`PIYO_EXIT_ON_STDIN_EOF`), so it does not outlive
//! the app even if the app is killed.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};

#[derive(Clone, Serialize)]
#[serde(tag = "state", rename_all = "lowercase")]
pub enum Status {
    Starting,
    Ready { port: u16, token: String },
    Failed { error: String },
}

#[derive(Deserialize)]
struct Hello {
    port: u16,
    token: String,
}

pub struct CoreState {
    status: Mutex<Status>,
    child: Mutex<Option<Child>>,
    /// Bumped on every launch so a thread for an old core cannot report on the new one.
    generation: AtomicU64,
}

impl CoreState {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            status: Mutex::new(Status::Starting),
            child: Mutex::new(None),
            generation: AtomicU64::new(0),
        })
    }

    fn set(&self, generation: u64, status: Status) -> bool {
        if self.generation.load(Ordering::SeqCst) != generation {
            return false;
        }
        *self.status.lock().unwrap() = status;
        true
    }

    /// Stops the current core, if any, including anything it started.
    pub fn stop(&self) {
        self.generation.fetch_add(1, Ordering::SeqCst);
        if let Some(mut child) = self.child.lock().unwrap().take() {
            kill_tree(&mut child);
            let _ = child.wait();
        }
    }
}

fn kill_tree(child: &mut Child) {
    #[cfg(windows)]
    {
        // `uv run` and venv launchers start the real interpreter as a child; kill the whole tree.
        use std::os::windows::process::CommandExt;
        let _ = Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .creation_flags(0x0800_0000)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    let _ = child.kill();
}

/// The command that starts the core: `uv run piyo-core` from the source tree in debug builds, the PyInstaller
/// bundle shipped in the app's resources (`core/piyo-core`, see `tauri.bundle.json`) in release builds.
fn core_command(app: &AppHandle) -> Result<Command, String> {
    let mut cmd = if cfg!(debug_assertions) {
        let core_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../core");
        let core_dir = core_dir
            .canonicalize()
            .map_err(|_| format!("Could not find the core folder at {}.", core_dir.display()))?;
        let mut cmd = Command::new("uv");
        cmd.args(["run", "piyo-core"]).current_dir(core_dir);
        cmd
    } else {
        let core_dir = app
            .path()
            .resource_dir()
            .map_err(|e| format!("Could not find the app's resources ({e})."))?
            .join("core");
        let exe = core_dir.join(if cfg!(windows) { "piyo-core.exe" } else { "piyo-core" });
        if !exe.is_file() {
            return Err(format!(
                "The Piyo core is missing from this installation ({}). Reinstall the app.",
                exe.display()
            ));
        }
        let mut cmd = Command::new(exe);
        cmd.current_dir(core_dir);
        cmd
    };
    // A fresh random port and token on every launch (the packaged core ignores these anyway).
    cmd.env_remove("PIYO_PORT")
        .env_remove("PIYO_TOKEN")
        .env("PIYO_EXIT_ON_STDIN_EOF", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    Ok(cmd)
}

pub fn launch(app: AppHandle, state: Arc<CoreState>) {
    let generation = state.generation.fetch_add(1, Ordering::SeqCst) + 1;
    *state.status.lock().unwrap() = Status::Starting;
    thread::spawn(move || {
        let outcome = run(&app, &state, generation);
        if let Err(error) = outcome {
            if state.set(generation, Status::Failed { error: error.clone() }) {
                let _ = app.emit("core-exited", error);
            }
        }
    });
}

/// Starts the core and blocks until it exits. Returns why, if it was not asked to stop.
fn run(app: &AppHandle, state: &Arc<CoreState>, generation: u64) -> Result<(), String> {
    let mut cmd = core_command(app)?;
    let mut child = cmd.spawn().map_err(|e| {
        if cfg!(debug_assertions) {
            format!("Could not start the core with `uv run piyo-core` ({e}). Is uv installed and on your PATH?")
        } else {
            format!("Could not start the Piyo core ({e}). Reinstall the app, or check that antivirus is not blocking it.")
        }
    })?;
    let stdout = child.stdout.take().ok_or("The core has no output.")?;
    let stderr = child.stderr.take().ok_or("The core has no error output.")?;
    *state.child.lock().unwrap() = Some(child);
    if state.generation.load(Ordering::SeqCst) != generation {
        state.stop();
        return Ok(());
    }

    let tail = Arc::new(Mutex::new(Vec::<String>::new()));
    let tail_writer = tail.clone();
    thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            let mut t = tail_writer.lock().unwrap();
            t.push(line);
            if t.len() > 8 {
                t.remove(0);
            }
        }
    });

    let mut lines = BufReader::new(stdout).lines();
    let hello = match lines.next() {
        Some(Ok(line)) => serde_json::from_str::<Hello>(&line).ok(),
        _ => None,
    };
    match hello {
        Some(h) => {
            // The line is printed before the server listens; only report ready once it accepts connections.
            if !wait_for_port(h.port) {
                return Err(exit_message(state, &tail, "did not start listening"));
            }
            state.set(generation, Status::Ready { port: h.port, token: h.token });
        }
        None => return Err(exit_message(state, &tail, "stopped before it was ready")),
    }
    // Keep draining so the core never blocks on a full pipe; EOF means it exited.
    for _ in lines {}
    if state.generation.load(Ordering::SeqCst) != generation {
        return Ok(());
    }
    Err(exit_message(state, &tail, "stopped unexpectedly"))
}

fn wait_for_port(port: u16) -> bool {
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    for _ in 0..300 {
        if std::net::TcpStream::connect_timeout(&addr, Duration::from_millis(200)).is_ok() {
            return true;
        }
        thread::sleep(Duration::from_millis(100));
    }
    false
}

fn exit_message(state: &CoreState, tail: &Mutex<Vec<String>>, what: &str) -> String {
    // Give the stderr reader a moment to catch up before quoting it.
    thread::sleep(Duration::from_millis(200));
    let code = state
        .child
        .lock()
        .unwrap()
        .as_mut()
        .and_then(|c| c.try_wait().ok().flatten())
        .and_then(|s| s.code());
    let mut msg = format!("The Piyo core {what}");
    if let Some(code) = code {
        msg.push_str(&format!(" (exit code {code})"));
    }
    msg.push('.');
    let tail = tail.lock().unwrap();
    if !tail.is_empty() {
        msg.push_str(" Last output: ");
        msg.push_str(&tail.join(" | "));
    }
    msg
}

#[tauri::command]
pub fn core_status(state: State<'_, Arc<CoreState>>) -> Status {
    state.status.lock().unwrap().clone()
}

#[tauri::command]
pub fn restart_core(app: AppHandle, state: State<'_, Arc<CoreState>>) {
    let state = state.inner().clone();
    // Report "starting" right away, or the webview would reconnect to the dead core's port.
    *state.status.lock().unwrap() = Status::Starting;
    // Stopping waits on the old process, so keep it off the command thread.
    thread::spawn(move || {
        state.stop();
        launch(app, state);
    });
}
