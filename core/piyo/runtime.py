"""Where things live when the core runs from source and when it is a PyInstaller bundle (the shipped sidecar).

A bundle has no repository around it: built-in skills and data files sit under `sys._MEIPASS`. (`uv` and Deno
are not in it; they are downloaded on first use, see `piyo/skills/helper_tools.py`.)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """The folder PyInstaller unpacked the bundle's data into."""
    return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))


def playwright_cache_dir() -> Path:
    """Where Playwright keeps downloaded browsers when nothing says otherwise."""
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"


def use_shared_browser_cache() -> None:
    """Playwright defaults PLAYWRIGHT_BROWSERS_PATH to 0 ("inside the package") when it runs frozen.

    Chromium is downloaded on first use (not shipped), and a bundle that is replaced on every update must not
    be where it lives, so point at Playwright's normal per-user cache, the same one a source run uses. A path
    the user set themselves stays.
    """
    if frozen() and os.environ.get("PLAYWRIGHT_BROWSERS_PATH") in (None, "", "0"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(playwright_cache_dir())
