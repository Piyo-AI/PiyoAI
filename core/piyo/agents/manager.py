"""Runs background agents side by side: queueing by priority, pause and resume, cancel, restart recovery.

The scheduler decides *when* an agent runs and keeps the held actions and notifications; this decides *how*:
each run gets its own `Agent` (built by the server's `execute`), waits for a run slot from the resource
broker (`runs` pool, higher priority first), and can be paused between model turns and resumed from its
saved conversation, also after a restart. Run records live in `scheduler.db` (`agent_runs`).

Every lease a run takes (run slot, model calls, the browser, scripts) is swept back to the broker when a
part of the run ends, whether it finished, paused, failed or was cancelled. A run asked to make room for a
higher-priority one (`preempted`) pauses at its checkpoint and queues again at once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from piyo.agents.broker import Holder, Lease, ResourceBroker, pool_capacity
from piyo.config.resource_limits import DEFAULT_PRESET, PRESETS
from piyo.safety import ApprovalDeferred, ApprovalRequest
from piyo.scheduler.service import Approver, RunResult
from piyo.scheduler.store import ACTIVE_STATES, AgentRun, Job, SchedulerStore

EVENT_QUEUE = 200  # per listener; a listener that falls this far behind misses events
PREEMPTED = "preempted"


@dataclass
class RunControl:
    """What the server's `execute` gets for one run: where to continue, and hooks back into the manager."""

    run_id: str
    job_id: str
    conversation_id: str | None  # continue this conversation; None starts a new one
    holder: Holder = field(default_factory=lambda: Holder("run"))  # what it takes leases as
    pause_reason: str | None = None
    cancel_requested: bool = False
    browser_lease: Lease | None = None
    paused: bool = False  # waiting at a checkpoint for `resume`
    held: int = 0
    task: asyncio.Task | None = None  # waiting for a run slot, or the agent segment that is running now
    resumed: asyncio.Event = field(default_factory=asyncio.Event)
    manager: AgentManager | None = None

    @property
    def has_browser(self) -> bool:
        return self.browser_lease is not None and not self.browser_lease.released

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
                self.browser_lease = await self.manager.broker.acquire("browser", self.holder)
            self.manager.publish({"type": "tool", "run_id": self.run_id, "job_id": self.job_id, "name": name})


Execute = Callable[[Job, Approver, RunControl], Awaitable[RunResult]]


class AgentManager:
    def __init__(self, store: SchedulerStore, execute: Execute, broker: ResourceBroker | None = None) -> None:
        self.store, self._execute = store, execute
        self.broker = broker or ResourceBroker(lambda pool: pool_capacity(PRESETS[DEFAULT_PRESET], pool))
        self._live: dict[str, RunControl] = {}
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

    # -- leases ----------------------------------------------------------------------------

    def _holder(self, control: RunControl, priority: str) -> Holder:
        def on_wait(pool: str) -> None:
            if pool != "runs":  # waiting for a run slot is "queued"
                self._set(control, state="waiting", reason=pool)

        def on_granted(pool: str) -> None:
            if pool != "runs":
                self._set(control, state="running", reason=None)

        def preempt() -> None:
            if control.pause_reason is None:  # a pause the user asked for wins
                control.pause_reason = PREEMPTED

        return Holder(control.run_id, priority, on_wait=on_wait, on_granted=on_granted, preempt=preempt)

    async def _slot(self, control: RunControl) -> None:
        # A task of its own, so `cancel` stops the wait the same way it stops a running part.
        control.task = asyncio.get_running_loop().create_task(self.broker.acquire("runs", control.holder))
        try:
            await control.task
        finally:
            control.task = None

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
        control.holder = self._holder(control, job.priority)
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
        queued_reason = None
        while True:
            self._set(control, state="queued", reason=queued_reason)
            try:
                await self._slot(control)
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
                control.browser_lease = None
                self.broker.release_all(control.holder)  # the sweep: nothing outlives this part of the run
            if result.outcome != "paused":
                return self._finish(control, result)
            control.conversation_id = result.conversation_id or control.conversation_id
            if control.pause_reason == PREEMPTED:  # made room for a higher-priority run: queue again now
                control.pause_reason = None
                queued_reason = PREEMPTED
                self._set(control, conversation_id=control.conversation_id)
                continue
            queued_reason = None
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
