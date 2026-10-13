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
from piyo.safety.scope import Scope
from piyo.scheduler.rules import RuleError, describe, next_run, parse_rule
from piyo.scheduler.store import AgentRun, Job, Pending, SchedulerStore

GRACE = timedelta(hours=24)  # how late a missed job may still run
GRACE_EVERY = timedelta(hours=1)  # a stale "every N minutes" run is not worth catching up
PENDING_TTL = timedelta(days=7)
TICK_SECONDS = 20.0


@dataclass
class RunResult:
    conversation_id: str | None
    outcome: str  # the agent's finish reason, "error", "cancelled", or "skipped" (still running from before)
    text: str = ""  # the final reply, for the notification
    error: str | None = None


Approver = Callable[[ApprovalRequest], Awaitable[bool]]
# (job, approver, run to continue or None) -> result. In the app this is `AgentManager.run`, which queues the
# run, runs the agent (in a conversation the user can open later) and waits through pauses.
Runner = Callable[[Job, Approver, AgentRun | None], Awaitable[RunResult]]
# (tool name, arguments, job id) -> output, run after the user approved a held action.
Executor = Callable[[str, dict, str], Awaitable[str]]
PRIORITIES = ("low", "normal", "high")
BUDGET_LIMITS = {"max_steps": (1, 200), "max_tokens": (1_000, 10_000_000), "timeout_s": (10, 86_400)}


def parse_budget(raw: object) -> dict | None:
    """Per-agent limits that replace the global run settings; None keeps the global ones."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RuleError("The budget must be an object.")
    if bad := set(raw) - set(BUDGET_LIMITS):
        raise RuleError(f"Unknown budget field(s): {', '.join(sorted(bad))}.")
    out = {}
    for key, value in raw.items():
        if value is None:
            continue
        low, high = BUDGET_LIMITS[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise RuleError(f"{key} must be a whole number from {low} to {high}.")
        out[key] = value
    return out or None


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
        self._task: asyncio.Task | None = None
        self._firing: set[asyncio.Task] = set()  # runs started by the clock; they run side by side

    # -- jobs ------------------------------------------------------------------------------

    def create(
        self,
        title: str,
        prompt: str,
        rule: object,
        provider: str,
        model: str,
        scope: Scope | None = None,
        priority: str = "normal",
        budget: object = None,
        manual: bool = False,
    ) -> Job:
        """A scheduled job (no `scope`: what scheduled runs could always do) or an agent (`scope` given;
        `manual` = no schedule, it runs when started)."""
        title, prompt = " ".join(title.split())[:80], prompt.strip()
        if not title or not prompt:
            raise RuleError("A scheduled job needs a title and something to do.")
        if len(prompt) > 4000:
            raise RuleError("The instructions are too long (limit 4000 characters).")
        if priority not in PRIORITIES:
            raise RuleError(f"priority must be one of {', '.join(PRIORITIES)}.")
        clean_budget = parse_budget(budget)
        if manual:
            clean, first = None, None
        else:
            clean = parse_rule(rule)
            first = next_run(clean, self._clock(), self._tz)
            if first is None:
                raise RuleError("That time has already passed.")
        return self.store.add_job(
            title,
            prompt,
            clean,
            provider,
            model,
            first.isoformat(timespec="seconds") if first else None,
            scope.to_json() if scope is not None else None,
            priority,
            clean_budget,
        )

    def update(self, job_id: str, **changes) -> Job:
        """`rule` None keeps the schedule; `manual` True removes it. Agents also take `scope`, `priority`,
        `budget`."""
        job = self.store.get_job(job_id)
        patch: dict = {}
        if (title := changes.get("title")) is not None:
            patch["title"] = " ".join(title.split())[:80] or job.title
        if (prompt := changes.get("prompt")) is not None and prompt.strip():
            patch["prompt"] = prompt.strip()[:4000]
        if (priority := changes.get("priority")) is not None:
            if priority not in PRIORITIES:
                raise RuleError(f"priority must be one of {', '.join(PRIORITIES)}.")
            patch["priority"] = priority
        if "budget" in changes and changes["budget"] is not None:
            patch["budget"] = parse_budget(changes["budget"])
        if (scope := changes.get("scope")) is not None:
            patch["scope"] = scope.to_json()
        if changes.get("manual"):
            rule = None
        else:
            rule = parse_rule(changes["rule"]) if changes.get("rule") is not None else job.rule
        enabled = job.enabled if changes.get("enabled") is None else bool(changes["enabled"])
        if rule != job.rule:
            patch["rule"] = rule
        if rule != job.rule or enabled != job.enabled:
            patch["enabled"] = enabled
            upcoming = next_run(rule, self._clock(), self._tz) if enabled and rule is not None else None
            if enabled and rule is not None and upcoming is None:
                raise RuleError("That time has already passed.")
            patch["next_run"] = upcoming.isoformat(timespec="seconds") if upcoming else None
        return self.store.update_job(job_id, **patch)

    def describe(self, job: Job) -> str:
        return describe(job.rule) if job.rule is not None else "When started"

    # -- running ---------------------------------------------------------------------------

    def _approver(self, job: Job, held: list[Pending]):
        async def hold(request: ApprovalRequest) -> bool:
            item = self.store.add_pending(
                job, request.tool, request.arguments, request.summary, self._clock()
            )
            held.append(item)
            raise ApprovalDeferred(
                "This is a scheduled run and the user is not here to approve. The action was NOT done; "
                "it was added to the user's pending approvals. Say that it is waiting for their "
                "approval and do not try again."
            )

        return hold

    async def fire(self, job: Job, run: AgentRun | None = None) -> None:
        """Run a job now (used by the clock, Run now, and to continue `run` after a restart); never raises.

        Runs of different jobs go side by side; how many at once is the runner's business (`AgentManager`).
        """
        held: list[Pending] = []
        result = RunResult(None, "error", error="The run did not start.")
        try:
            result = await self._runner(job, self._approver(job, held), run)
        except Exception as e:  # a bad provider or key must not stop the scheduler
            result = RunResult(None, "error", error=str(e) or type(e).__name__)
        if result.outcome == "skipped":
            self.store.add_event(
                "missed", f"{job.title} was skipped", "It was still running from the last time.", job.id
            )
            return
        if result.error:
            status = "error"
        elif result.outcome == "cancelled":
            status = "cancelled"
        else:
            status = "waiting" if held else "done"
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
        elif status != "cancelled":  # the user stopped it, no need to tell them
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

    def launch(self, job: Job, run: AgentRun | None = None) -> asyncio.Task:
        """`fire` in the background; the task is kept so it is not garbage-collected mid-run."""
        task = asyncio.get_running_loop().create_task(self.fire(job, run))
        self._firing.add(task)
        task.add_done_callback(self._firing.discard)
        return task

    async def tick(self, wait: bool = True) -> None:
        """Start every due job. `wait` (tests) returns when they are done; the clock does not wait, so a long
        run never delays the next job."""
        now = self._clock()
        started: list[asyncio.Task] = []
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
            started.append(self.launch(job))
        if wait and started:
            await asyncio.gather(*started)

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick(wait=False)
            except Exception:
                pass  # one bad tick must not end the scheduler
            await asyncio.sleep(self._tick_seconds)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        tasks = [t for t in (self._task, *self._firing) if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
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
            output = await self._executor(item.tool, item.arguments, item.job_id)
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
