"""Scheduling tools: the model can set up reminders and routines, but only with the user's approval, because
a schedule keeps acting later without anyone watching (see `piyo.scheduler`)."""

from __future__ import annotations

from piyo.scheduler import RuleError, Scheduler, describe, parse_rule
from piyo.tools.base import Risk, RunContext, Tool

WHEN = {
    "type": "object",
    "description": (
        "When to run. One of: {kind: once, at: 'YYYY-MM-DDTHH:MM'} (use current_time to work out dates), "
        "{kind: daily, time: 'HH:MM'}, {kind: weekdays, time: 'HH:MM'}, "
        "{kind: weekly, days: [0..6 with Monday=0], time: 'HH:MM'}, {kind: every, minutes: N (at least 5)}. "
        "Times are the user's local clock."
    ),
}


def schedule_tools(scheduler: Scheduler) -> list[Tool]:
    async def create(args: dict, ctx: RunContext) -> str:
        if not ctx.provider_id or not ctx.model:
            raise ValueError("Scheduling is not available in this run.")
        try:
            job = scheduler.create(
                str(args.get("title") or ""),
                str(args.get("prompt") or ""),
                args.get("when"),
                ctx.provider_id,
                ctx.model,
            )
        except RuleError as e:
            raise ValueError(str(e)) from None
        return f"Scheduled [{job.id}] {job.title}: {describe(job.rule)}. Next run {job.next_run} UTC."

    async def listing(args: dict, ctx: RunContext) -> str:
        jobs = scheduler.store.list_jobs()
        if not jobs:
            return "Nothing is scheduled."
        return "\n".join(
            f"[{j.id}] {j.title}: {describe(j.rule)}"
            + ("" if j.enabled else " (off)")
            + f" | next: {j.next_run or 'none'}"
            for j in jobs
        )

    async def cancel(args: dict, ctx: RunContext) -> str:
        job_id = str(args.get("id") or "")
        try:
            job = scheduler.store.get_job(job_id)
        except KeyError:
            raise ValueError(f"No scheduled job with id {job_id!r}. Use schedule.list to find it.") from None
        scheduler.store.delete_job(job_id)
        return f"Cancelled {job.title}."

    def create_summary(args: dict) -> str:
        try:
            when = describe(parse_rule(args.get("when")))
        except RuleError:
            when = "an unclear time"
        return f"Schedule {str(args.get('title'))[:80]!r} ({when}): {str(args.get('prompt'))[:200]}"

    def cancel_summary(args: dict) -> str:
        try:
            return f"Cancel the scheduled job {scheduler.store.get_job(str(args.get('id'))).title!r}"
        except KeyError:
            return "Cancel a scheduled job"

    return [
        Tool(
            name="schedule.create",
            description=(
                "Set up a reminder or a routine that Piyo runs later on its own, for example "
                "'every morning at 8, give me my brief'. The prompt is what you will be asked to do at "
                "that time, written as if the user said it. The user approves each schedule. Scheduled "
                "runs cannot send or change anything without the user approving afterwards."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short name, under 80 characters"},
                    "prompt": {"type": "string", "description": "What to do when it runs"},
                    "when": WHEN,
                },
                "required": ["title", "prompt", "when"],
            },
            handler=create,
            risk=Risk.CONFIRM,
            summarize=create_summary,
            core=True,
        ),
        Tool(
            name="schedule.list",
            description="List the reminders and routines that are set up.",
            handler=listing,
            core=True,
        ),
        Tool(
            name="schedule.cancel",
            description="Delete a scheduled job by id (from schedule.list).",
            parameters={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            handler=cancel,
            risk=Risk.CONFIRM,
            summarize=cancel_summary,
            core=True,
        ),
    ]
