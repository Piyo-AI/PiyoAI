"""Run the same checks as CI: `python scripts/check.py` (or `uv run python ...`) from the repo root."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NPM = shutil.which("npm") or "npm"
UV = shutil.which("uv") or "uv"

STEPS = [
    ("ruff", [UV, "run", "ruff", "check", "piyo", "tests"], ROOT / "core"),
    ("pytest", [UV, "run", "pytest", "-q"], ROOT / "core"),
    ("typecheck", [NPM, "run", "typecheck"], ROOT / "apps" / "desktop"),
    ("build", [NPM, "run", "build"], ROOT / "apps" / "desktop"),
]


def main() -> int:
    failed = []
    for name, cmd, cwd in STEPS:
        print(f"\n== {name} ==", flush=True)
        if subprocess.run(cmd, cwd=cwd).returncode != 0:
            failed.append(name)
    print("\nFAILED: " + ", ".join(failed) if failed else "\nAll checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
