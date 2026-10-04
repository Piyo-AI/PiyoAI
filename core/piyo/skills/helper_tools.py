"""`uv` and Deno: the programs that run a skill's scripts, downloaded the first time a script needs one.

They are not shipped in the installer (together they are 130 MB, and most people never run a script).
The first script that needs one downloads it from the pinned release URL in `helper_tools.json`, checks
the file's SHA-256 against the pinned hash, and only then unpacks it. A mismatch deletes the download and
nothing runs: these programs run skill code, so a swapped release must never be used. Nothing is looked up
at run time (no "latest" query); to upgrade, change the version and every hash together (CONTRIBUTING.md).

Which copy a script gets: a path the user set (`PIYO_UV`, `PIYO_DENO`), then our pinned download, then a
program of that name on the user's PATH (so a developer's own install is used and nothing is downloaded).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import shutil
import stat
import sys
import tarfile
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from piyo.config import tools_dir

PINS = Path(__file__).with_name("helper_tools.json")
ENV = {"uv": "PIYO_UV", "deno": "PIYO_DENO"}
NAMES = {"uv": "uv", "deno": "Deno"}
MAX_DOWNLOAD_BYTES = 300 * 2**20

# (url, where to write, progress(received, total or None)): the real one is `http_download`.
Fetch = Callable[[str, Path, Callable[[int, int | None], None]], Awaitable[None]]


class HelperError(Exception):
    """The message goes back to the model and the user, so keep it actionable."""


@dataclass
class HelperStatus:
    tool: str
    version: str
    state: str  # missing | installed | installing | failed
    percent: int = 0
    message: str = ""
    approx_mb: int = 0
    path: str | None = None


def platform_key() -> str:
    system = {"win32": "windows", "linux": "linux", "darwin": "macos"}.get(sys.platform)
    machine = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(
        platform.machine().lower()
    )
    if not system or not machine:
        raise HelperError(f"There is no download of uv or Deno for {sys.platform}/{platform.machine()}.")
    return f"{system}-{machine}"


def exe_name(tool: str) -> str:
    return tool + (".exe" if sys.platform == "win32" else "")


def load_pins() -> dict:
    return json.loads(PINS.read_text(encoding="utf-8"))


async def http_download(url: str, target: Path, progress: Callable[[int, int | None], None]) -> None:
    """Stream `url` into `target`. Only https, also after redirects (GitHub hands releases elsewhere)."""
    if not url.startswith("https://"):
        raise HelperError("Only https downloads are allowed.")
    async with (
        httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=60.0)) as client,
        client.stream("GET", url, headers={"User-Agent": "PiyoAI"}) as res,
    ):
        if res.url.scheme != "https":
            raise HelperError("The download was redirected to a page that is not https, so it was refused.")
        res.raise_for_status()
        length = res.headers.get("content-length", "")
        total = int(length) if length.isdigit() else None
        received = 0
        with target.open("wb") as out:
            async for chunk in res.aiter_bytes(1 << 20):
                received += len(chunk)
                if received > MAX_DOWNLOAD_BYTES:
                    raise HelperError("The download was larger than expected, so it was stopped.")
                out.write(chunk)
                progress(received, total)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _unpack(archive: Path, program: str, out: Path) -> None:
    """Copy the one executable called `program` out of a zip or tar archive to `out`."""
    with out.open("wb") as dest:
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                member = next((n for n in zf.namelist() if Path(n).name == program), None)
                if member is None:
                    raise HelperError(f"The download has no {program} in it.")
                with zf.open(member) as src:
                    shutil.copyfileobj(src, dest)
        else:
            with tarfile.open(archive) as tf:
                found = next(
                    (m for m in tf.getmembers() if m.isfile() and Path(m.name).name == program), None
                )
                if found is None:
                    raise HelperError(f"The download has no {program} in it.")
                src = tf.extractfile(found)
                assert src is not None
                shutil.copyfileobj(src, dest)
    out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class HelperTools:
    def __init__(self, folder: Path | None = None, fetch: Fetch = http_download) -> None:
        self._folder = folder
        self._fetch = fetch
        self._state: dict[str, HelperStatus] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    @property
    def folder(self) -> Path:
        return self._folder or tools_dir()

    # -- where it is --------------------------------------------------------------------------

    def _pin(self, tool: str) -> dict:
        pin = load_pins().get(tool)
        if pin is None:
            raise HelperError(f"Unknown helper program {tool!r}.")
        return pin

    def _downloaded(self, tool: str) -> Path:
        return self.folder / f"{tool}-{self._pin(tool)['version']}" / exe_name(tool)

    def path(self, tool: str) -> str | None:
        """The program to use right now, or None when it has to be downloaded."""
        chosen = os.environ.get(ENV[tool])
        if chosen and Path(chosen).is_file():
            return chosen
        if (ours := self._downloaded(tool)).is_file():
            return str(ours)
        return shutil.which(tool)

    def status(self, tool: str) -> HelperStatus:
        pin = self._pin(tool)
        now = self._state.get(tool)
        if now is not None:
            return now
        found = self.path(tool)
        return HelperStatus(
            tool,
            pin["version"],
            "installed" if found else "missing",
            approx_mb=int(pin.get("approx_mb", 0)),
            path=found,
        )

    def download_note(self, tool: str) -> str:
        """A sentence for the approval card when running now would start a download, else ''."""
        if self.path(tool):
            return ""
        pin = self._pin(tool)
        return (
            f"The first run downloads {NAMES[tool]} {pin['version']} (about {pin.get('approx_mb', '?')} MB) "
            "from github.com, checked against a fixed fingerprint."
        )

    # -- getting it ---------------------------------------------------------------------------

    def _begin(self, tool: str) -> asyncio.Task:
        task = self._tasks.get(tool)
        if task is None or task.done():
            self._state[tool] = HelperStatus(
                tool, self._pin(tool)["version"], "installing", 0, f"Downloading {NAMES[tool]}…"
            )
            task = self._tasks[tool] = asyncio.ensure_future(self._install(tool))
        return task

    async def ensure(self, tool: str) -> str:
        """The program's path, downloading it first if needed. Concurrent callers share one download."""
        if found := self.path(tool):
            return found
        await asyncio.shield(self._begin(tool))  # a stopped run does not stop the download for the next one
        if found := self.path(tool):
            return found
        raise HelperError(self.status(tool).message or f"{NAMES[tool]} is not available.")

    def start(self, tool: str) -> HelperStatus:
        """Begin the download in the background (the app's button); no-op when present or already running."""
        if self.path(tool) is None:
            self._begin(tool)
        return self.status(tool)

    async def _install(self, tool: str) -> None:
        pin = self._pin(tool)
        version, name = pin["version"], NAMES[tool]

        def say(state: str, percent: int = 0, message: str = "") -> None:
            self._state[tool] = HelperStatus(
                tool, version, state, percent, message, int(pin.get("approx_mb", 0))
            )

        folder = self.folder
        staged = folder / f".{tool}-{version}-{os.getpid()}.exe"
        archive = None
        try:
            folder.mkdir(parents=True, exist_ok=True)
            entry = pin["files"].get(platform_key())
            if entry is None:
                raise HelperError(f"There is no download of {name} for {platform_key()}.")
            archive = (
                folder / f".{os.getpid()}-{entry['file']}"
            )  # keeps .zip / .tar.gz, which says how to open it
            url = pin["url"].format(version=version, file=entry["file"])

            def progress(received: int, total: int | None) -> None:
                say("installing", min(99, received * 100 // total) if total else 0, f"Downloading {name}…")

            await self._fetch(url, archive, progress)
            say("installing", 99, f"Checking {name}…")
            if await asyncio.to_thread(_sha256, archive) != entry["sha256"]:
                raise HelperError(
                    f"The {name} download does not match its fixed fingerprint, so it was deleted, not used."
                )
            await asyncio.to_thread(_unpack, archive, exe_name(tool), staged)
            target = self._downloaded(tool)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
            for old in folder.glob(f"{tool}-*"):  # earlier versions
                if old != target.parent and old.is_dir():
                    shutil.rmtree(old, ignore_errors=True)
            self._state.pop(tool, None)
        except HelperError as e:
            say("failed", 0, str(e))
        except (httpx.HTTPError, OSError, zipfile.BadZipFile, tarfile.TarError) as e:
            say(
                "failed",
                0,
                f"Could not download {name}: {_why(e)}. Check your internet connection and try again.",
            )
        finally:
            if archive is not None:
                archive.unlink(missing_ok=True)
            staged.unlink(missing_ok=True)


def _why(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"the server answered {e.response.status_code}"
    return type(e).__name__
