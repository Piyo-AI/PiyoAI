//! Panic notes for crash reports. Opt-in reporting lives in the core (`piyo/telemetry.py`); the shell only
//! leaves a note it can pick up: one `crate/src/file.rs:line:column` per panic, never the panic message
//! (it can contain anything) and never an absolute path (it would contain the user's name).
//!
//! The core reads the note on its next start, queues it only if the user opted in, and deletes it either way.

use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;

use tauri::{AppHandle, Manager};

pub const NOTE_FILE: &str = "shell-crash.log";

/// Where the note goes: next to the shell's other state files.
pub fn note_path(app: &AppHandle) -> Option<PathBuf> {
    let dir = app.path().app_local_data_dir().ok()?;
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir.join(NOTE_FILE))
}

/// `src/lib.rs:12:5` for our own files, `crate-1.0/src/lib.rs:12:5` for dependencies.
pub fn label(file: &str, line: u32, column: u32) -> String {
    let normal = file.replace('\\', "/");
    let parts: Vec<&str> = normal.split('/').filter(|p| !p.is_empty()).collect();
    let short = match parts.iter().rposition(|p| *p == "src") {
        Some(0) => parts.join("/"),
        Some(i) => parts[i - 1..].join("/"),
        None => parts.last().copied().unwrap_or("unknown").to_string(),
    };
    format!("{short}:{line}:{column}")
}

/// Record the location of every panic in `path`, then run the normal panic handler.
pub fn install(path: PathBuf) {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        if let Some(at) = info.location() {
            if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&path) {
                let _ = writeln!(file, "{}", label(at.file(), at.line(), at.column()));
            }
        }
        previous(info);
    }));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn own_files_keep_only_the_crate_relative_path() {
        assert_eq!(label("src/rollback.rs", 42, 9), "src/rollback.rs:42:9");
        assert_eq!(label("src\\core.rs", 7, 1), "src/core.rs:7:1");
    }

    #[test]
    fn dependency_paths_lose_the_users_folders() {
        let file = r"C:\Users\bob\.cargo\registry\src\index.crates.io-6f17d22bba15001f\tauri-2.1.0\src\lib.rs";
        let text = label(file, 3, 4);
        assert_eq!(text, "tauri-2.1.0/src/lib.rs:3:4");
        assert!(!text.contains("bob"));
    }

    #[test]
    fn a_path_without_src_keeps_only_the_file_name() {
        assert_eq!(label("/home/bob/build/main.rs", 1, 2), "main.rs:1:2");
    }
}
