"""Per-user paths and settings storage (cross-platform)."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import PlatformDirs

APP_NAME = "PiyoAI"
APP_AUTHOR = "Piyo AI"

_dirs = PlatformDirs(APP_NAME, APP_AUTHOR, roaming=True)


def data_dir() -> Path:
    """Root for user data: installed skills, browser profile, database, logs.

    Overridable with PIYO_DATA_DIR (used by tests and portable installs).
    """
    override = os.environ.get("PIYO_DATA_DIR")
    path = Path(override) if override else Path(_dirs.user_data_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_dir() -> Path:
    """Where Piyo puts files it creates when the user has not named a folder: `~/Piyo`.

    Overridable with PIYO_WORKSPACE_DIR. Not created here; the file tools create it on first use.
    """
    override = os.environ.get("PIYO_WORKSPACE_DIR")
    return Path(override).expanduser() if override else Path.home() / "Piyo"


def skills_dir() -> Path:
    path = data_dir() / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def browser_profile_dir() -> Path:
    path = data_dir() / "browser-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path
