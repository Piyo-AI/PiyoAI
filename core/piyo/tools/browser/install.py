"""The one-time Chromium download for Piyo's browser.

Chromium is not bundled (it is ~150 MB per OS). Piyo never downloads it on its own: the user starts the
download from the app, and `BrowserInstaller` reports progress while Playwright's own installer runs.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from piyo.runtime import frozen

# Playwright prints a progress bar like `|■■■■    | 40% of 150.2 MiB`, redrawn with carriage returns.
_PERCENT = re.compile(r"(\d{1,3})%")
FAILED = "The download did not finish. Check your internet connection and try again."


@dataclass
class InstallStatus:
    state: str  # unknown | missing | installed | installing | failed
    percent: int = 0
    message: str = ""


Check = Callable[[], Awaitable[bool]]
Spawn = Callable[[], AsyncIterator[bytes]]  # output chunks of the installer; raises if it exits non-zero


async def chromium_installed() -> bool:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        return Path(p.chromium.executable_path).exists()


def installer_command() -> tuple[list[str], dict | None]:
    """The command that downloads Chromium, and its environment (None: inherit ours).

    From source, `python -m playwright` does it. In the packaged core `sys.executable` is the core itself,
    so the Node driver inside the playwright package is run directly (what `python -m playwright` does).
    """
    if not frozen():
        return [sys.executable, "-m", "playwright", "install", "chromium"], None
    from playwright._impl._driver import compute_driver_executable, get_driver_env

    node, cli = compute_driver_executable()
    return [node, cli, "install", "chromium"], get_driver_env()


async def run_playwright_install() -> AsyncIterator[bytes]:
    command, env = installer_command()
    proc = await asyncio.create_subprocess_exec(
        *command, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )  # fmt: skip
    assert proc.stdout is not None
    try:
        while chunk := await proc.stdout.read(512):
            yield chunk
        if await proc.wait() != 0:
            raise RuntimeError("installer failed")
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


class BrowserInstaller:
    def __init__(self, check: Check = chromium_installed, spawn: Spawn = run_playwright_install) -> None:
        self._check = check
        self._spawn = spawn
        self._status = InstallStatus("unknown")
        self._task: asyncio.Task | None = None

    def status(self) -> InstallStatus:
        return self._status

    async def refresh(self) -> InstallStatus:
        """Look at the disk (unless a download is running) and report."""
        if self._status.state != "installing":
            try:
                found = await self._check()
            except Exception as e:
                print(f"Could not check for Piyo's browser: {e!r}", file=sys.stderr, flush=True)
                found = False
            self._status = InstallStatus("installed" if found else "missing")
        return self._status

    def start(self) -> InstallStatus:
        """Begin the download in the background; a second call while one runs changes nothing."""
        if self._status.state != "installing":
            self._status = InstallStatus("installing", 0, "Downloading Piyo's browser…")
            self._task = asyncio.ensure_future(self._run())
        return self._status

    async def _run(self) -> None:
        try:
            async for chunk in self._spawn():
                found = _PERCENT.findall(chunk.decode("utf-8", errors="replace"))
                if found:
                    percent = max(0, min(int(found[-1]), 100))
                    self._status = InstallStatus("installing", percent, "Downloading Piyo's browser…")
            ok = await self._check()
        except Exception:
            self._status = InstallStatus("failed", 0, FAILED)
            return
        self._status = (
            InstallStatus("installed", 100, "Piyo's browser is ready.")
            if ok
            else InstallStatus("failed", 0, FAILED)
        )

    async def close(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
