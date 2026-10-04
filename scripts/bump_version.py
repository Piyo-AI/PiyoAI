"""Set the app version everywhere it is written, then check the files agree.

    python scripts/bump_version.py 0.1.1

Covers the five files `check_version.py` compares and the three lock files that repeat the version.
Then commit, tag `v<version>` and push the tag: the release workflow builds the installers.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (file, regex whose group 1 is the version; first match only)
PATTERNS = [
    ("apps/desktop/src-tauri/Cargo.toml", r'(?m)^version = "([^"]+)"'),
    ("apps/desktop/src-tauri/Cargo.lock", r'name = "piyo-desktop"\nversion = "([^"]+)"'),
    ("core/pyproject.toml", r'(?m)^version = "([^"]+)"'),
    ("core/uv.lock", r'name = "piyo"\nversion = "([^"]+)"'),
    ("core/piyo/__init__.py", r'__version__ = "([^"]+)"'),
]
JSON_FILES = ["apps/desktop/src-tauri/tauri.conf.json", "apps/desktop/package.json"]


def replace_group(text: str, pattern: str, new: str) -> str:
    m = re.search(pattern, text)
    if not m:
        raise SystemExit(f"No version found for {pattern!r}")
    return text[: m.start(1)] + new + text[m.end(1) :]


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        print(__doc__)
        return 2
    new = sys.argv[1]
    for rel, pattern in PATTERNS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        path.write_text(replace_group(text, pattern, new), encoding="utf-8", newline="\n")
    for rel in JSON_FILES:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        path.write_text(replace_group(text, r'"version": "([^"]+)"', new), encoding="utf-8", newline="\n")
    # package-lock.json repeats it for the root package, in two places near the top.
    lock = ROOT / "apps/desktop/package-lock.json"
    data = json.loads(lock.read_text(encoding="utf-8"))
    data["version"] = new
    data["packages"][""]["version"] = new
    lock.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return subprocess.call([sys.executable, str(ROOT / "scripts/check_version.py")])


if __name__ == "__main__":
    sys.exit(main())
