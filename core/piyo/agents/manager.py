"""Runs background agents side by side: queueing by priority, pause and resume, cancel, restart recovery.

The scheduler decides *when* an agent runs and keeps the held actions and notifications; this decides *how*:
each run gets its own `Agent` (built by the server's `execute`), waits for a free slot (`max_parallel`, higher
priority first), and can be paused between model turns and resumed from its saved conversation, also after a
restart. Run records live in `scheduler.db` (`agent_runs`).

Until the resource broker exists (docs/phase-9, workstream B), two simple limits stand in for it: a fixed
number of runs at once, and one background run at a time in the signed-in browser profile (taken at its first
browser call, held until the run ends or pauses).
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from piyo.safety import ApprovalDeferred, ApprovalRequest
from piyo.scheduler.service import Approver, RunResult
from piyo.scheduler.store import ACTIVE_STATES, AgentRun, Job, SchedulerStore

DEFAULT_PARALLEL = 3
RANK = {"low": 0, "normal": 1, "high": 2}
EVENT_QUEUE = 200  # per listener; a listener that falls this far behind misses events


@dataclass
class RunControl:
    """What the server's `execute` gets for one run: where to continue, and hooks back into the manager."""

    run_id: str
    job_id: str
    conversation_id: str | None  # continue this conversation; None starts a new one
    pause_reason: str | None = None
    cancel_requested: bool = False
    has_browser: bool = False
    paused: bool = False  # waiting at a checkpoint for `resume`
    held: int = 0
    task: asyncio.Task | None = None  # the agent segment that is running now
    waiter: asyncio.Future | None = None  # the slot it is queued for
    resumed: asyncio.Event = field(default_factory=asyncio.Event)
    manager: AgentManager | None = None

    def should_pause(self) -> bool:
        """Asked by the agent loop between model turns."""
        return self.pause_reason is not None

    def started(self, conversation_id: str) -> None:
        """The run has its conversation; saved at once so a crash can continue it."""
        self.conversation_id = conversation_id
        if self.manager:
            self.manager._set(self, conversation_id=conversation_id)

    async def tool_starting(self, name: str) -> None:
        """Called before a tool runs. Browser tools wait for the browser profile if another run has it."""
        if self.manager:
            if name.startswith(("browser.", "browser__")) and not self.has_browser:
                await self.manager._take_browser(self)
            self.manager.publish({"type": "tool", "run_id": self.run_id, "job_id": self.job_id, "name": name})


Execute = Callable[[Job, Approver, RunControl], Awaitable[RunResult]]


class AgentManager:
    def __init__(self, store: SchedulerStore, execute: Execute, max_parallel: int = DEFAULT_PARALLEL) -> None:
        self.store, self._execute, self.max_parallel = store, execute, max_parallel
        self._live: dict[str, RunControl] = {}
        self._running = 0
        self._queue: list[tuple[int, int, asyncio.Future]] = []
        self._order = itertools.count()
        self._browser = asyncio.Lock()
        self._listeners: set[asyncio.Queue] = set()
        # Starts a run again in the background (the scheduler's `launch`, which also records how it ended).
        self.relaunch: Callable[[Job, AgentRun], object] | None = None

    # -- events ----------------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(EVENT_QUEUE)
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._listeners.discard(queue)

    def publish(self, event: dict) -> None:
        for queue in list(self._listeners):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a stuck listener must not hold up the runs

    def _set(self, control: RunControl, **changes) -> AgentRun:
        run = self.store.update_run(control.run_id, **changes)
        self.publish({"type": "run", "run": vars(run)})
        return run

    # -- slots -----------------------------------------------------------------------------

    async def _slot(self, control: RunControl, priority: str) -> None:
        if self._running < self.max_parallel and not self._queue:
            self._running += 1
            return
        waiter = asyncio.get_running_loop().create_future()
        control.waiter = waiter
        heapq.heappush(self._queue, (-RANK.get(priority, 1), next(self._order), waiter))
        try:
            await waiter  # when it resolves, the slot was handed over: `_running` already counts it
        except asyncio.CancelledError:
            if waiter.done() and not waiter.cancelled():
                self._free_slot()  # handed over just as it was cancelled: pass it on
            raise
        finally:
            control.waiter = None

    def _free_slot(self) -> None:
        while self._queue:
            _, _, waiter = heapq.heappop(self._queue)
            if not waiter.done():
                waiter.set_result(None)
                return
        self._running -= 1

    async def _take_browser(self, control: RunControl) -> None:
        if self._browser.locked():
            self._set(control, state="waiting", reason="browser")
        await self._browser.acquire()
        control.has_browser = True
        self._set(control, state="running", reason=None)

    def _drop_browser(self, control: RunControl) -> None:
        if control.has_browser:
            control.has_browser = False
            self._browser.release()

    # -- running ---------------------------------------------------------------------------

    def is_active(self, job_id: str) -> bool:
        return any(c.job_id == job_id for c in self._live.values())

    async def run(self, job: Job, approver: Approver, run: AgentRun | None = None) -> RunResult:
        """Run `job` (or continue `run`) to the end, waiting through pauses. The scheduler's runner."""
        if run is None:
            if self.is_active(job.id):
                return RunResult(None, "skipped")
            run = self.store.add_run(job.id)
        elif run.id in self._live:
            return RunResult(run.conversation_id, "skipped")
        control = RunControl(run.id, job.id, run.conversation_id, held=run.held, manager=self)
        self._live[run.id] = control

        async def counted(request: ApprovalRequest) -> bool:
            try:
                return await approver(request)
            except ApprovalDeferred:
                control.held += 1
                self._set(control, held=control.held)
                raise

        try:
            return await self._run(job, control, counted)
        finally:
            self._live.pop(run.id, None)

    async def _run(self, job: Job, control: RunControl, approver: Approver) -> RunResult:
        while True:
            self._set(control, state="queued", reason=None)
            try:
                await self._slot(control, job.priority)
            except asyncio.CancelledError:
                return self._cancelled(control)
            try:
                if control.pause_reason is None:
                    self._set(control, state="running", reason=None)
                    segment = self._execute(job, approver, control)
                    control.task = asyncio.get_running_loop().create_task(segment)
                    result = await control.task
                else:  # paused while it waited for a slot
                    result = RunResult(control.conversation_id, "paused")
            except asyncio.CancelledError:
                return self._cancelled(control)
            except Exception as e:  # the server's execute reports its own errors; this is a bug guard
                result = RunResult(control.conversation_id, "error", error=str(e) or type(e).__name__)
            finally:
                control.task = None
                self._drop_browser(control)
                self._free_slot()
            if result.outcome != "paused":
                return self._finish(control, result)
            control.conversation_id = result.conversation_id or control.conversation_id
            self._set(
                control, state="paused", reason=control.pause_reason, conversation_id=control.conversation_id
            )
            control.paused = True
            try:
                await control.resumed.wait()
            except asyncio.CancelledError:
                return self._cancelled(control)
            finally:
                control.paused = False
            control.resumed.clear()
            if control.cancel_requested:
                return self._cancelled(control)
            control.pause_reason = None

    def _cancelled(self, control: RunControl) -> RunResult:
        """The user stopped the run. A shutdown cancels too: then the run stays as it is for `recover`."""
        task = asyncio.current_task()
        if not control.cancel_requested or (task is not None and task.cancelling()):
            raise asyncio.CancelledError
        self._set(control, state="cancelled", reason=None)
        return RunResult(control.conversation_id, "cancelled")

    def _finish(self, control: RunControl, result: RunResult) -> RunResult:
        if result.error:
            self._set(control, state="failed", reason=result.error[:300])
        elif result.outcome == "cancelled":
            self._set(control, state="cancelled", reason=None)
        else:
            self._set(control, state="needs-you" if control.held else "done", reason=None)
        return result

    # -- control ---------------------------------------------------------------------------

    def pause(self, run_id: str, reason: str = "user") -> AgentRun:
        """Pause at the next checkpoint (between model turns); a tool call in progress finishes first."""
        control = self._live.get(run_id)
        if control is None:
            raise ValueError("This run is not running.")
        control.pause_reason = reason
        return self.store.get_run(run_id)

    def resume(self, run_id: str) -> AgentRun:
        run = self.store.get_run(run_id)
        control = self._live.get(run_id)
        if control is not None:
            if control.pause_reason is None:
                raise ValueError("This run is not paused.")
            if control.paused:
                control.resumed.set()  # the loop clears the reason when it wakes
            else:
                control.pause_reason = None  # asked to pause, not there yet: just go on
            return run
        if run.state != "paused":
            raise ValueError("This run is not paused.")
        job = self.store.get_job(run.job_id)  # KeyError: the agent was deleted
        if self.relaunch is None:
            raise ValueError("Runs cannot be started right now.")
        self.relaunch(job, run)
        return run

    def cancel(self, run_id: str) -> AgentRun:
        control = self._live.get(run_id)
        if control is None:
            run = self.store.get_run(run_id)
            if run.state not in ACTIVE_STATES:
                raise ValueError("This run has already ended.")
            return self.store.update_run(run_id, state="cancelled", reason=None)  # paused before a restart
        control.cancel_requested = True
        if control.task is not None:
            control.task.cancel()
        elif control.waiter is not None:
            control.waiter.cancel()
        else:
            control.resumed.set()  # paused: wake it so it ends
        return self.store.get_run(run_id)

    def cancel_job(self, job_id: str) -> None:
        for control in list(self._live.values()):
            if control.job_id == job_id:
                self.cancel(control.run_id)

    def recover(self) -> None:
        """After a restart: runs that were going are continued; a run the user paused stays paused."""
        for run in self.store.list_runs(active_only=True, limit=500):
            if run.id in self._live:
                continue
            try:
                job = self.store.get_job(run.job_id)
            except KeyError:
                self.store.update_run(run.id, state="cancelled", reason="The agent was deleted.")
                continue
            if run.state == "paused" and run.reason == "user":
                continue
            run = self.store.update_run(run.id, state="paused", reason="restart")
            if self.relaunch is not None:
                self.relaunch(job, run)
