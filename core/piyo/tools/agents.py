"""Background agent tools: the model can propose an agent, but the user approves it and sees its whole scope
on the card, because an agent keeps working later without anyone watching (see `piyo.agents`)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from piyo.agents import AgentManager
from piyo.safety.scope import NEW_AGENT, Scope, ScopeError, parse_scope
from piyo.scheduler import PRIORITIES, RuleError, Scheduler, describe, parse_rule
from piyo.tools.base import Risk, RunContext, Tool
from piyo.tools.schedule import WHEN

NAMES = {"type": "array", "items": {"type": "string"}}


def _scope(args: dict) -> Scope:
    raw = {k: args[k] for k in ("skills", "folders", "browser") if args.get(k) is not None}
    if args.get("google_accounts") is not None:
        raw["accounts"] = args["google_accounts"]
    return parse_scope(raw, default=NEW_AGENT)


def agent_tools(
    scheduler: Scheduler, manager: AgentManager, approved: Callable[[], list[Path]] = list
) -> list[Tool]:
    def check_folders(scope: Scope) -> None:
        roots = {p.resolve() for p in approved()}
        for f in scope.folders or ():
            if Path(f).expanduser().resolve() not in roots:
                raise ValueError(f"{f} is not an approved folder; the user can add it in Settings > Folders.")

    async def create(args: dict, ctx: RunContext) -> str:
        if not ctx.provider_id or not ctx.model:
            raise ValueError("Background agents are not available in this run.")
        try:
            scope = _scope(args)
            check_folders(scope)
            manual = args.get("when") is None
            job = scheduler.create(
                str(args.get("title") or ""),
                str(args.get("prompt") or ""),
                args.get("when"),
                ctx.provider_id,
                ctx.model,
                scope=scope,
                priority=str(args.get("priority") or "normal"),
                manual=manual,
            )
        except (RuleError, ScopeError) as e:
            raise ValueError(str(e)) from None
        if manual:
            scheduler.launch(job)
            return (
                f"Started the agent [{job.id}] {job.title} in the background. The user gets a notification "
                "when it is done; it cannot send or change anything without their approval."
            )
        return f"Created the agent [{job.id}] {job.title}: {describe(job.rule)}. Next run {job.next_run} UTC."

    async def listing(args: dict, ctx: RunContext) -> str:
        jobs = scheduler.store.list_jobs()
        if not jobs:
            return "There are no agents or scheduled jobs."
        active = {r.job_id: r for r in scheduler.store.list_runs(active_only=True, limit=500)}
        lines = []
        for j in jobs:
            if run := active.get(j.id):
                state = run.state + (f" ({run.reason})" if run.reason else "")
            else:
                state = j.last_status or "has not run"
            lines.append(f"[{j.id}] {j.title}: {scheduler.describe(j)} | {state}")
        return "\n".join(lines)

    async def cancel(args: dict, ctx: RunContext) -> str:
        job_id = str(args.get("id") or "")
        try:
            job = scheduler.store.get_job(job_id)
        except KeyError:
            raise ValueError(f"No agent with id {job_id!r}. Use agent.list to find it.") from None
        if not manager.is_active(job.id):
            return f"{job.title} is not running."
        manager.cancel_job(job.id)
        return f"Stopped {job.title}. It is still set up; it runs again at its next time or when started."

    def create_summary(args: dict) -> str:
        when = args.get("when")
        try:
            timing = describe(parse_rule(when)) if when is not None else "starts now, once"
        except RuleError:
            timing = "an unclear time"
        try:
            scope = _scope(args).describe()
        except ScopeError as e:
            scope = f"an invalid scope ({e})"
        return (
            f"Create the background agent {str(args.get('title'))[:80]!r} ({timing}): "
            f"{str(args.get('prompt'))[:200]}. It may use {scope}."
        )

    def cancel_summary(args: dict) -> str:
        try:
            return f"Stop the agent {scheduler.store.get_job(str(args.get('id'))).title!r}"
        except KeyError:
            return "Stop an agent"

    return [
        Tool(
            name="agent.create",
            description=(
                "Hand a task to a background agent that works on it alone while the user does other "
                "things, now (leave out 'when') or on a schedule. Give it only what it needs: the skills it "
                "may load, approved folders beyond its own folder, Google accounts, and the browser ('fresh' "
                "clean browser, 'profile' the signed-in one, 'off'). The user approves each agent. Agents "
                "cannot send or change anything without the user approving afterwards."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short name, under 80 characters"},
                    "prompt": {"type": "string", "description": "The task, written as if the user said it"},
                    "when": {**WHEN, "description": WHEN["description"] + " Leave out to start now, once."},
                    "skills": {**NAMES, "description": "Skills it may load; leave out for any enabled skill"},
                    "folders": {**NAMES, "description": "Approved folders it may use besides its own folder"},
                    "google_accounts": {**NAMES, "description": "Google accounts it may use; omit for any"},
                    "browser": {"type": "string", "enum": ["fresh", "profile", "off"]},
                    "priority": {"type": "string", "enum": list(PRIORITIES)},
                },
                "required": ["title", "prompt"],
            },
            handler=create,
            risk=Risk.CONFIRM,
            summarize=create_summary,
            core=True,
        ),
        Tool(
            name="agent.list",
            description="List the background agents and scheduled jobs and what each is doing now.",
            handler=listing,
            core=True,
        ),
        Tool(
            name="agent.cancel",
            description="Stop a running background agent by id (from agent.list). It stays set up.",
            parameters={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            handler=cancel,
            risk=Risk.CONFIRM,
            summarize=cancel_summary,
            core=True,
        ),
    ]
