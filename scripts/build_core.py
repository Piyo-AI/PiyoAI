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
import tempfile
import time
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
    # uv and Deno are not bundled: the core downloads them the first time a script needs one (the smoke test does it).


def call(base: str, auth: dict, method: str, path: str):
    request = urllib.request.Request(base + path, method=method, headers=auth)
    return json.loads(urllib.request.urlopen(request, timeout=30).read().decode())


def path_without(*programs: str) -> str:
    """PATH minus the folders that hold these programs, so the smoke test cannot use the runner's own copies."""
    suffix = ".exe" if sys.platform == "win32" else ""
    folders = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(f for f in folders if not any((Path(f) / (p + suffix)).exists() for p in programs))


def smoke() -> None:
    """Start the built core like the app does, ask it for its health, download uv and Deno, and stop it."""
    exe = OUT / ("piyo-core.exe" if sys.platform == "win32" else "piyo-core")
    data = Path(tempfile.mkdtemp(prefix="piyo-smoke-"))  # the downloads go here, not into the user's real folder
    env = {**os.environ, "PIYO_EXIT_ON_STDIN_EOF": "1", "PIYO_TOKEN": "must-be-ignored", "PIYO_PORT": "1"}
    env |= {"PATH": path_without("uv", "deno"), "PIYO_DATA_DIR": str(data)}
    for name in ("PIYO_UV", "PIYO_DENO"):
        env.pop(name, None)
    proc = subprocess.Popen(
        [str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
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
                    time.sleep(0.2)
            assert body, f"{path} did not answer: {proc.stderr.read()[-800:] if proc.poll() is not None else ''}"
            assert expect is None or expect in body, f"{path} answered without {expect!r}: {body[:200]}"
        # The first-use download, as a script run would start it: pinned URL, hash check, unpack, and it runs.
        pins = json.loads((CORE / "piyo" / "skills" / "helper_tools.json").read_text())
        for tool in ("uv", "deno"):
            status = call(base, auth, "POST", f"/api/helper-tools/{tool}/install")
            for _ in range(600):
                if status["state"] != "installing":
                    break
                time.sleep(0.5)
                status = next(t for t in call(base, auth, "GET", "/api/helper-tools") if t["tool"] == tool)
            assert status["state"] == "installed", f"{tool} did not download: {status}"
            assert Path(status["path"]).is_relative_to(data), f"{tool} came from {status['path']}, not the download"
            out = subprocess.run([status["path"], "--version"], capture_output=True, text=True, timeout=30).stdout
            assert pins[tool]["version"] in out, f"downloaded {tool} reports {out!r}, expected {pins[tool]['version']}"
        print("smoke test passed:", base, "| browser:", body)
    finally:
        shutil.rmtree(data, ignore_errors=True)
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
