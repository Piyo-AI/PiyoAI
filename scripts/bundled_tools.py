"""Download the pinned `uv` and Deno releases into the packaged core's `bin/` folder.

The versions, file names and SHA-256 hashes live in `core/bundled-tools.json`. A download whose hash differs
is deleted and the build stops: these programs run skill code, so a swapped release must never be bundled
silently. To upgrade, change the version and the hashes together (see CONTRIBUTING.md), nothing is looked up
at build time.
"""

from __future__ import annotations

import hashlib
import json
import platform
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PINS = ROOT / "core" / "bundled-tools.json"
CACHE = ROOT / "core" / ".tool-cache"
PROGRAMS = {"uv": "uv", "deno": "deno"}  # tool -> the executable's name inside the archive


def platform_key() -> str:
    system = {"win32": "windows", "linux": "linux", "darwin": "macos"}.get(sys.platform)
    machine = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(
        platform.machine().lower()
    )
    if not system or not machine:
        raise SystemExit(f"No bundled uv/Deno for {sys.platform}/{platform.machine()}.")
    return f"{system}-{machine}"


def exe_name(name: str) -> str:
    return name + (".exe" if sys.platform == "win32" else "")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def download(tool: str, pin: dict, entry: dict) -> Path:
    """The archive in the cache, downloaded if needed, always checked against the pinned hash."""
    CACHE.mkdir(exist_ok=True)
    target = CACHE / f"{tool}-{pin['version']}-{entry['file']}"
    if not target.exists() or sha256_of(target) != entry["sha256"]:
        url = pin["url"].format(version=pin["version"], file=entry["file"])
        print(f"downloading {url}", flush=True)
        request = urllib.request.Request(url, headers={"User-Agent": "PiyoAI-build"})
        with urllib.request.urlopen(request, timeout=120) as res, target.open("wb") as out:
            while chunk := res.read(1 << 20):
                out.write(chunk)
        found = sha256_of(target)
        if found != entry["sha256"]:
            target.unlink()
            raise SystemExit(f"{entry['file']}: hash {found} does not match the pinned {entry['sha256']}.")
    return target


def unpack(archive: Path, program: str, dest: Path) -> Path:
    wanted = exe_name(program)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / wanted
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            member = next((n for n in zf.namelist() if Path(n).name == wanted), None)
            if member is None:
                raise SystemExit(f"{archive.name} has no {wanted}.")
            out.write_bytes(zf.read(member))
    else:
        with tarfile.open(archive) as tf:
            member = next((m for m in tf.getmembers() if m.isfile() and Path(m.name).name == wanted), None)
            if member is None:
                raise SystemExit(f"{archive.name} has no {wanted}.")
            out.write_bytes(tf.extractfile(member).read())
    out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return out


def install_tools(dest: Path) -> dict[str, str]:
    """Put uv and Deno into `dest`; returns {tool: pinned version}."""
    pins = json.loads(PINS.read_text())
    key = platform_key()
    versions = {}
    for tool, program in PROGRAMS.items():
        pin = pins[tool]
        if key not in pin["files"]:
            raise SystemExit(f"{tool} has no pinned download for {key}.")
        unpack(download(tool, pin, pin["files"][key]), program, dest)
        versions[tool] = pin["version"]
    return versions
