"""Check that every place that carries the app's version agrees, and (given a tag) that the tag matches.

    python scripts/check_version.py            # the files agree
    python scripts/check_version.py v0.1.0     # ... and equal the tag (release workflow)
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def versions() -> dict[str, str]:
    cargo = tomllib.loads((ROOT / "apps/desktop/src-tauri/Cargo.toml").read_text())["package"]["version"]
    init = re.search(r'__version__ = "([^"]+)"', (ROOT / "core/piyo/__init__.py").read_text())
    return {
        "apps/desktop/src-tauri/tauri.conf.json": json.loads(
            (ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_text()
        )["version"],
        "apps/desktop/src-tauri/Cargo.toml": cargo,
        "apps/desktop/package.json": json.loads((ROOT / "apps/desktop/package.json").read_text())["version"],
        "core/pyproject.toml": tomllib.loads((ROOT / "core/pyproject.toml").read_text())["project"]["version"],
        "core/piyo/__init__.py": init.group(1) if init else "?",
    }


def main() -> int:
    found = versions()
    if len(set(found.values())) != 1:
        print("The version differs between files:")
        for path, version in found.items():
            print(f"  {version:10} {path}")
        return 1
    version = next(iter(found.values()))
    if len(sys.argv) > 1 and sys.argv[1].removeprefix("v") != version:
        print(f"The tag {sys.argv[1]} does not match the version in the files ({version}).")
        return 1
    print(f"version {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
