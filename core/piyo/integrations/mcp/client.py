"""One stdio session with one MCP server, through the official `mcp` SDK.

The server process starts on first use and keeps running for later calls; it stops with the core, when the
user changes or removes the server, or when it crashes (the next call starts it again, with a growing wait
after repeated failures). Server-initiated requests (sampling, elicitation, roots) are declined: the SDK
refuses them unless a callback is given, and none is. The server's stderr goes to a log file the app shows
the end of.

The SDK's anyio contexts must be entered and left in one task, so a task of its own owns the session; calls
from runs use the session object, which is safe across tasks of the same event loop.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp import types as mt

START_TIMEOUT_S = 60.0  # first start may download the server (npx, uvx)
CALL_TIMEOUT_S = 120.0
PING_TIMEOUT_S = 5.0
MAX_PAGES = 20
LOG_TAIL_BYTES = 8000


class McpError(Exception):
    """A server that could not start or answer; the message is shown to the model and the user."""


def _reason(exc: BaseException) -> str:
    """The useful part of an error, also from inside an anyio exception group."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return str(exc) or type(exc).__name__


Launch = Callable[[], Awaitable[StdioServerParameters]]


class McpClient:
    def __init__(
        self, name: str, launch: Launch, log_path: Path, clock: Callable[[], float] = time.monotonic
    ):
        self.name, self._launch, self.log_path, self._clock = name, launch, log_path, clock
        self.state = "stopped"  # stopped | starting | running | failed
        self.error: str | None = None
        self.verified = False  # the tool list was checked against the pins since this start
        self._session: ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None
        self._lock = asyncio.Lock()
        self._failures = 0
        self._retry_at = 0.0

    @property
    def running(self) -> bool:
        return self._session is not None and self._task is not None and not self._task.done()

    async def session(self) -> ClientSession:
        async with self._lock:
            if self.running:
                return self._session  # type: ignore[return-value]
            if self._failures and self._clock() < self._retry_at:
                wait = int(self._retry_at - self._clock()) + 1
                raise McpError(
                    f"The MCP server {self.name!r} failed to start ({self.error}); trying again in {wait} s."
                )
            loop = asyncio.get_running_loop()
            ready: asyncio.Future = loop.create_future()
            self._stop = asyncio.Event()
            self.state, self.error, self.verified = "starting", None, False
            self._task = loop.create_task(self._own(ready, self._stop))
            try:
                self._session = await asyncio.wait_for(asyncio.shield(ready), START_TIMEOUT_S)
            except BaseException as e:
                await self._end_task()
                self._failed(
                    f"did not start within {START_TIMEOUT_S:g} s"
                    if isinstance(e, TimeoutError)
                    else _reason(e)
                )
                if isinstance(e, asyncio.CancelledError):
                    raise
                raise McpError(f"The MCP server {self.name!r} could not start: {self.error}") from None
            self._failures, self.state = 0, "running"
            return self._session

    def _failed(self, reason: str) -> None:
        self._failures += 1
        # One failure restarts at the next use; repeated ones wait longer each time, up to 5 minutes.
        wait = min(5 * 2 ** (self._failures - 2), 300) if self._failures > 1 else 0
        self._retry_at = self._clock() + wait
        self.state, self.error, self._session = "failed", reason, None

    async def _own(self, ready: asyncio.Future, stop: asyncio.Event) -> None:
        try:
            params = await self._launch()
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("w", encoding="utf-8", errors="replace") as errlog:
                async with stdio_client(params, errlog=errlog) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        ready.set_result(session)
                        await stop.wait()
        except BaseException as e:
            if not ready.done():
                ready.set_exception(McpError(_reason(e)))
            elif not stop.is_set() and not isinstance(e, asyncio.CancelledError):
                self._failed(f"stopped unexpectedly: {_reason(e)}")
            if isinstance(e, asyncio.CancelledError):
                raise
        else:
            if not stop.is_set():
                self._failed("stopped unexpectedly")
        finally:
            self._session = None
            if stop.is_set():
                self.state = "stopped"

    async def list_tools(self) -> list[mt.Tool]:
        session = await self.session()
        tools, cursor = [], None
        for _ in range(MAX_PAGES):
            params = mt.PaginatedRequestParams(cursor=cursor) if cursor else None
            page = await self._timed(session.list_tools(params=params), "list its tools")
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                break
        return tools

    async def call(self, tool: str, arguments: dict, timeout: float | None = None) -> mt.CallToolResult:
        timeout = timeout or CALL_TIMEOUT_S
        session = await self.session()
        return await self._timed(
            session.call_tool(tool, arguments), f"run {tool}", timeout
        )

    async def _timed(self, work: Awaitable, what: str, timeout: float | None = None):
        timeout = timeout or CALL_TIMEOUT_S
        try:
            return await asyncio.wait_for(work, timeout)
        except TimeoutError:
            await self._check_alive()
            raise McpError(f"The MCP server {self.name!r} did not {what} within {timeout:g} s.") from None
        except Exception as e:
            await self._check_alive()
            raise McpError(f"The MCP server {self.name!r} could not {what}: {_reason(e)}") from None

    async def _check_alive(self) -> None:
        """After a failed request: a server that does not answer a ping is dropped (next use restarts it)."""
        session = self._session
        if session is None:
            return
        try:
            await asyncio.wait_for(session.send_ping(), PING_TIMEOUT_S)
        except Exception:
            async with self._lock:
                if self._session is session:
                    await self._end_task()
                    self._failed("stopped answering")

    async def stop(self) -> None:
        async with self._lock:
            await self._end_task()
            self.state, self.verified, self._failures = "stopped", False, 0

    async def _end_task(self) -> None:
        task, self._task, self._session = self._task, None, None
        if self._stop is not None:
            self._stop.set()
        if task is None or task.done():
            return
        try:  # closing its stdin lets the server exit; the SDK kills it after a grace period
            await asyncio.wait_for(asyncio.shield(task), 10)
        except Exception:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def log_tail(self, lines: int = 20) -> list[str]:
        try:
            with self.log_path.open("rb") as f:
                f.seek(0, 2)
                f.seek(max(f.tell() - LOG_TAIL_BYTES, 0))
                text = f.read().decode("utf-8", "replace")
        except OSError:
            return []
        return [line for line in text.splitlines() if line.strip()][-lines:]
