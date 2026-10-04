"""Build the packaged core (the desktop app's sidecar): `python scripts/build_core.py` from the repo root.

Output: `core/dist/piyo-core/` (an executable plus its libraries). Tauri bundles that folder as a resource
(`apps/desktop/src-tauri/tauri.conf.json`). `--smoke` also launches the result and checks that it answers.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"
OUT = CORE / "dist" / "piyo-core"
UV = shutil.which("uv") or "uv"


def build() -> None:
    shutil.rmtree(CORE / "build", ignore_errors=True)
    shutil.rmtree(OUT, ignore_errors=True)
    # Its own environment (no dev tools, plus PyInstaller): a running dev core that locks `.venv` cannot get in the way,
    # and nothing the tests need ends up in the bundle.
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": ".venv-build"}
    subprocess.run([UV, "sync", "--locked", "--no-dev", "--group", "build"], cwd=CORE, env=env, check=True)
    subprocess.run([UV, "run", "--no-sync", "pyinstaller", "--noconfirm", "piyo-core.spec"], cwd=CORE, env=env, check=True)


def smoke() -> None:
    """Start the built core like the app does, ask it for its health, and stop it."""
    exe = OUT / ("piyo-core.exe" if sys.platform == "win32" else "piyo-core")
    proc = subprocess.Popen(
        [str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PIYO_EXIT_ON_STDIN_EOF": "1", "PIYO_TOKEN": "must-be-ignored", "PIYO_PORT": "1"},
    )  # fmt: skip
    try:
        hello = json.loads(proc.stdout.readline())
        assert hello["token"] != "must-be-ignored" and hello["port"] != 1, "the packaged core obeyed the environment"
        base = f"http://127.0.0.1:{hello['port']}"
        auth = {"Authorization": f"Bearer {hello['token']}"}
        # skills: the bundled built-in skills are found; providers: keyring loaded a backend (it reads each key);
        # browser install: the Playwright driver inside the bundle starts ("missing" is fine, an error is not).
        checks = (
            ("/api/health", None),
            ("/api/skills", "builtin"),
            ("/api/providers", "deepseek"),
            ("/api/browser/install", '"state"'),
        )
        for path, expect in checks:
            request = urllib.request.Request(base + path, headers=auth)
            body = None
            for _ in range(50):  # the line is printed before the server listens
                try:
                    body = urllib.request.urlopen(request, timeout=5).read().decode()
                    break
                except OSError:
                    import time

                    time.sleep(0.2)
            assert body, f"{path} did not answer: {proc.stderr.read()[-800:] if proc.poll() is not None else ''}"
            assert expect is None or expect in body, f"{path} answered without {expect!r}: {body[:200]}"
        print("smoke test passed:", base, "| browser:", body)
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if errors := proc.stderr.read().strip():
            print("core stderr:", errors[-1500:])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="run the built core and check it answers")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    if not args.skip_build:
        build()
    if args.smoke:
        smoke()
