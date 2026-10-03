"""Runs scheduled jobs while the core is running.

The core only runs while the app is open (no tray or start-at-login yet), so a job whose time passed while the
app was closed is handled when it starts: run once if it is not too late, otherwise record it as missed.
A scheduled run is unattended, so anything that needs the user's approval is held in a pending list instead of
happening; the user approves or declines it later, and only then does it run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

from piyo.safety import ApprovalDeferred, ApprovalRequest
from piyo.scheduler.rules import RuleError, describe, next_run, parse_rule
from piyo.scheduler.store import Job, Pending, SchedulerStore

GRACE = timedelta(hours=24)  # how late a missed job may still run
GRACE_EVERY = timedelta(hours=1)  # a stale "every N minutes" run is not worth catching up
PENDING_TTL = timedelta(days=7)
TICK_SECONDS = 20.0


@dataclass
class RunResult:
    conversation_id: str | None
    outcome: str  # the agent's finish reason, or "error"
    text: str = ""  # the final reply, for the notification
    error: str | None = None


# (job, approver) -> result. The server builds it: a normal agent run in a fresh conversation.
Runner = Callable[[Job, Callable[[ApprovalRequest], Awaitable[bool]]], Awaitable[RunResult]]
# (tool name, arguments) -> output, run after the user approved a held action.
Executor = Callable[[str, dict], Awaitable[str]]


def _now() -> datetime:
    return datetime.now(UTC)


class Scheduler:
    def __init__(
        self,
        store: SchedulerStore,
        runner: Runner,
        executor: Executor,
        clock: Callable[[], datetime] = _now,
        tz: tzinfo | None = None,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self.store, self._runner, self._executor, self._clock, self._tz = store, runner, executor, clock, tz
        self._tick_seconds = tick_seconds
        self._lock = asyncio.Lock()  # one scheduled run at a time: they share the browser and the model
        self._task: asyncio.Task | None = None

    # -- jobs ------------------------------------------------------------------------------

    def create(self, title: str, prompt: str, rule: object, provider: str, model: str) -> Job:
        title, prompt = " ".join(title.split())[:80], prompt.strip()
        if not title or not prompt:
            raise RuleError("A scheduled job needs a title and something to do.")
        if len(prompt) > 4000:
            raise RuleError("The instructions are too long (limit 4000 characters).")
        clean = parse_rule(rule)
        first = next_run(clean, self._clock(), self._tz)
        if first is None:
            raise RuleError("That time has already passed.")
        return self.store.add_job(title, prompt, clean, provider, model, first.isoformat(timespec="seconds"))

    def update(self, job_id: str, **changes) -> Job:
        job = self.store.get_job(job_id)
        patch: dict = {}
        if (title := changes.get("title")) is not None:
            patch["title"] = " ".join(title.split())[:80] or job.title
        if (prompt := changes.get("prompt")) is not None and prompt.strip():
            patch["prompt"] = prompt.strip()[:4000]
        rule = parse_rule(changes["rule"]) if changes.get("rule") is not None else job.rule
        enabled = job.enabled if changes.get("enabled") is None else bool(changes["enabled"])
        if rule != job.rule:
            patch["rule"] = rule
        if rule != job.rule or enabled != job.enabled:
            patch["enabled"] = enabled
            upcoming = next_run(rule, self._clock(), self._tz) if enabled else None
            if enabled and upcoming is None:
                raise RuleError("That time has already passed.")
            patch["next_run"] = upcoming.isoformat(timespec="seconds") if upcoming else None
        return self.store.update_job(job_id, **patch)

    def describe(self, job: Job) -> str:
        return describe(job.rule)

    # -- running ---------------------------------------------------------------------------

    def _approver(self, job: Job, held: list[Pending]):
        async def hold(request: ApprovalRequest) -> bool:
            item = self.store.add_pending(job, request.tool, request.arguments, request.summary)
            held.append(item)
            raise ApprovalDeferred(
                "This is a scheduled run and the user is not here to approve. The action was NOT done; "
                "it was added to the user's pending approvals. Say that it is waiting for their "
                "approval and do not try again."
            )

        return hold

    async def fire(self, job: Job) -> None:
        """Run a job now (used by the clock and by Run now); never raises."""
        async with self._lock:
            held: list[Pending] = []
            result = RunResult(None, "error", error="The run did not start.")
            try:
                result = await self._runner(job, self._approver(job, held))
            except Exception as e:  # a bad provider or key must not stop the scheduler
                result = RunResult(None, "error", error=str(e) or type(e).__name__)
            status = "error" if result.error else ("waiting" if held else "done")
            for item in held:
                self.store.set_pending(item.id, "pending", conversation_id=result.conversation_id)
            try:
                self.store.update_job(
                    job.id,
                    last_run=self._clock().isoformat(timespec="seconds"),
                    last_status=status,
                    last_conversation_id=result.conversation_id,
                )
            except KeyError:
                return  # deleted while it ran
            if result.error:
                self.store.add_event(
                    "failed", f"{job.title} could not run", result.error, job.id, result.conversation_id
                )
            else:
                self.store.add_event(
                    "finished",
                    f"{job.title} is done",
                    result.text or "Finished.",
                    job.id,
                    result.conversation_id,
                )
            for item in held:
                self.store.add_event(
                    "approval",
                    f"{job.title} needs your approval",
                    item.summary,
                    job.id,
                    result.conversation_id,
                )

    async def run_now(self, job_id: str) -> None:
        await self.fire(self.store.get_job(job_id))

    async def tick(self) -> None:
        now = self._clock()
        for job in self.store.due_jobs(now):
            late = now - datetime.fromisoformat(job.next_run)
            grace = GRACE_EVERY if job.rule["kind"] == "every" else GRACE
            upcoming = next_run(job.rule, now, self._tz)
            # Move the clock forward first: a crash during the run must not make it fire again and again.
            self.store.update_job(
                job.id,
                next_run=upcoming.isoformat(timespec="seconds") if upcoming else None,
                enabled=job.enabled and upcoming is not None,
            )
            if late > grace:
                self.store.update_job(job.id, last_status="missed")
                self.store.add_event(
                    "missed", f"{job.title} was missed", "Piyo was not running at the scheduled time.", job.id
                )
                continue
            await self.fire(job)

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                pass  # one bad tick must not end the scheduler
            await asyncio.sleep(self._tick_seconds)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # -- held actions ----------------------------------------------------------------------

    async def approve(self, pending_id: str) -> Pending:
        item = self.store.get_pending(pending_id)
        if item.status != "pending":
            raise ValueError("This was already handled.")
        if self._clock() - datetime.fromisoformat(item.created_at) > PENDING_TTL:
            self.store.set_pending(pending_id, "expired")
            raise ValueError("This request is too old to approve. Ask Piyo to do it again.")
        try:
            output = await self._executor(item.tool, item.arguments)
        except Exception as e:
            self.store.set_pending(pending_id, "failed", str(e) or type(e).__name__)
            raise ValueError(f"It did not work: {e}") from None
        self.store.set_pending(pending_id, "approved", output[:500])
        return self.store.get_pending(pending_id)

    def decline(self, pending_id: str) -> Pending:
        item = self.store.get_pending(pending_id)
        if item.status != "pending":
            raise ValueError("This was already handled.")
        self.store.set_pending(pending_id, "declined")
        return self.store.get_pending(pending_id)
