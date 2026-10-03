"""Tools that ship with the core and need no integration."""

from __future__ import annotations

from datetime import datetime

from piyo.models.capabilities import model_issues
from piyo.tools.base import RunContext, Tool


async def _load_skill(args: dict, ctx: RunContext) -> str:
    name = str(args.get("name", ""))
    skill = ctx.skills.get(name)
    if skill is None:
        known = ", ".join(s.manifest.name for s in ctx.skills.enabled()) or "none"
        return f"No skill named {name!r}. Available skills: {known}."
    if ctx.model_caps and (issues := model_issues(skill.manifest.requires.model, ctx.model_caps)):
        return (
            f"The skill {name!r} can't run with the current model: {'; '.join(issues)}. "
            "It was not loaded. Tell the user this and what to change; don't attempt the task "
            "without the skill."
        )
    ctx.active_skills.add(skill.manifest.name)
    parts = [f"# Skill: {skill.manifest.name}", "", skill.body.strip()]
    if skill.scripts:
        parts += ["", f"Skill directory: {skill.path}", "Scripts: " + ", ".join(skill.scripts)]
    needs = skill.manifest.requires.tools
    if needs:
        parts += ["", "Tools unlocked by this skill: " + ", ".join(needs)]
    return "\n".join(parts)


async def _current_time(args: dict, ctx: RunContext) -> str:
    return datetime.now().astimezone().strftime("%A %Y-%m-%d %H:%M %Z (UTC%z)")


def core_tools() -> list[Tool]:
    return [
        Tool(
            name="load_skill",
            description=(
                "Load the full instructions of a skill from the skill list. Call this before "
                "doing a task a skill covers; it also unlocks the tools that skill needs."
            ),
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Skill name"}},
                "required": ["name"],
            },
            handler=_load_skill,
            core=True,
        ),
        Tool(
            name="current_time",
            description="The user's current local date, time and timezone.",
            handler=_current_time,
            core=True,
        ),
    ]
