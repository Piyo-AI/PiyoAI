"""Places Piyo's file tools never touch, and folders too broad to approve.

Anything the file tools read goes to the model provider, so two mistakes would be costly: approving a whole
drive or home folder (which holds keys, browser profiles and password files), and a key file sitting inside a
folder that was approved for something harmless. `broad_reason` stops the first when a folder is approved;
`protected_reason` stops the second on every file call, whatever folder it sits in.

Matching is by name and lower-cased (Windows and macOS file systems ignore case). It is a safety net for
the common cases, not a promise that no secret can ever be read: a password in `notes.txt` is not recognised.
"""

from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path

from piyo.config import data_dir

# A folder with one of these names, anywhere in the path, holds credentials.
PROTECTED_DIRS = {
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".password-store", ".terraform.d", ".gcloud",
    "keychains",
}  # fmt: skip
PROTECTED_FILES = [
    ".env", ".env.*", "*.env", ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials", "id_rsa*",
    "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.pem", "*.key", "*.pfx", "*.p12", "*.kdbx", "*.keystore",
    "*.jks", "known_hosts", "authorized_keys", "logins.json", "key4.db", "cert9.db", "secrets.json",
    "credentials.json", "token.json",
]  # fmt: skip
# Browser profiles (saved passwords, cookies, history), as consecutive path parts.
BROWSER_PROFILES = [
    ("google", "chrome"), ("chromium",), ("microsoft", "edge"), ("bravesoftware",), ("mozilla", "firefox"),
    ("firefox", "profiles"), ("opera software",), ("vivaldi",), ("google-chrome",), ("brave-browser",),
    ("application support", "google", "chrome"), ("application support", "firefox"),
]  # fmt: skip
# Everything under these is system. (On macOS, temporary folders live under /private/var/folders, so /var and
# /private are not blocked as a whole.)
SYSTEM_TREES = {
    "windows": [os.environ.get("SystemRoot"), os.environ.get("ProgramFiles"),
                os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramData")],
    "posix": ["/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sbin", "/sys", "/usr", "/System",
              "/Library", "/private/etc"],
}  # fmt: skip
# These folders themselves hold everyone's files; a subfolder of them (a person's Documents) is fine.
SYSTEM_FOLDERS = {
    "windows": ["C:\\Users"],
    "posix": ["/Users", "/home", "/var", "/private", "/opt", "/root", "/mnt", "/media", "/Volumes"],
}  # fmt: skip


def _parts(path: Path) -> list[str]:
    return [p.lower() for p in path.parts]


def _contains(parts: list[str], run: tuple[str, ...]) -> bool:
    n = len(run)
    return any(tuple(parts[i : i + n]) == run for i in range(len(parts) - n + 1))


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _inside(path: Path, folder: Path) -> bool:
    return _same(path, folder) or any(_same(parent, folder) for parent in path.parents)


def protected_reason(path: Path) -> str | None:
    """Why Piyo never reads, lists or changes this path (already resolved), or None."""
    parts = _parts(path)
    if any(name in PROTECTED_DIRS for name in parts):
        return "it is inside a folder that holds keys or credentials"
    if any(_contains(parts, run) for run in BROWSER_PROFILES):
        return "it is part of a web browser's saved data (passwords, cookies)"
    if parts and any(fnmatch.fnmatch(parts[-1], pattern) for pattern in PROTECTED_FILES):
        return "it looks like a key, password or secrets file"
    try:
        if _inside(path, data_dir().resolve()):
            return "it is Piyo's own data (conversations, settings)"
    except OSError:
        pass
    return None


def broad_reason(path: Path) -> str | None:
    """Why this folder is too broad to approve for file access (already resolved), or None."""
    if path.parent == path:
        return "That is a whole drive."
    home = Path.home().resolve()
    if _same(path, home) or any(_same(path, ancestor) for ancestor in home.parents):
        return "That is your whole home folder. Approve the folders you want Piyo to work in, like Documents."
    kind = "windows" if sys.platform == "win32" else "posix"
    if any(_inside(path, Path(f)) for f in SYSTEM_TREES[kind] if f) or any(
        _same(path, Path(f)) for f in SYSTEM_FOLDERS[kind]
    ):
        return "That is a system folder."
    if reason := protected_reason(path):
        return f"Piyo does not work in that folder: {reason}."
    return None
