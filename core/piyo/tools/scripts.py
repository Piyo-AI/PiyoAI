"""`skill.run_script`: run a helper script that a loaded skill ships (see `piyo.skills.runner`)."""

from __future__ import annotations

import json

from piyo.safety.untrusted import wrap_untrusted
from piyo.skills import SkillRegistry
from piyo.skills.runner import ScriptError, ScriptRunner
from piyo.tools.base import Risk, RunContext, Tool


def script_tools(runner: ScriptRunner, skills: SkillRegistry) -> list[Tool]:
    async def run(args: dict, ctx: RunContext) -> str:
        name, script = str(args.get("skill") or ""), str(args.get("script") or "")
        skill = ctx.skills.get(name)
        if skill is None or name not in ctx.active_skills:
            raise ValueError(
                f"Load the skill {name!r} first (load_skill); only a loaded skill can run its scripts."
            )
        extra = args.get("args") or {}
        if not isinstance(extra, dict):
            raise ValueError("args must be a JSON object.")
        try:
            result = await runner.run(skill, script, extra)
        except ScriptError as e:
            raise ValueError(str(e)) from None
        text = json.dumps(result.value, ensure_ascii=False, indent=2)
        return wrap_untrusted(text, f"script {script} of skill {name}")

    def risk_for(args: dict) -> Risk:
        # Our own skills are trusted to run their scripts; anything the user installed asks every time.
        skill = skills.get(str(args.get("skill") or ""))
        return Risk.AUTO if skill is not None and skill.source == "builtin" else Risk.CONFIRM

    def summarize(args: dict) -> str:
        shown = json.dumps(args.get("args") or {}, ensure_ascii=False)[:200]
        return f"Run the script {args.get('script')} of the skill {args.get('skill')} with {shown}"

    return [
        Tool(
            name="skill.run_script",
            description=(
                "Run one of the helper scripts a loaded skill lists under 'Scripts'. Pass the skill "
                "name, the script file name and a JSON object of arguments; you get back the JSON the "
                "script printed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "script": {"type": "string", "description": "File name from the skill's script list"},
                    "args": {"type": "object", "description": "Input for the script"},
                },
                "required": ["skill", "script"],
            },
            handler=run,
            risk=Risk.CONFIRM,
            risk_for=risk_for,
            summarize=summarize,
            core=True,
        )
    ]
