"""The agent loop: model turn -> tool calls -> results -> repeat until the model is done.

`Agent.run` appends to the caller's message list, so the caller keeps the conversation state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from piyo.agent.prompts import build_system_prompt
from piyo.models.providers import Provider
from piyo.models.turn import (
    Message,
    TextDelta,
    ToolCall,
    TurnDone,
    TurnEvent,
    stream_turn,
)
from piyo.safety import PermissionGate
from piyo.skills import SkillRegistry
from piyo.tools import RunContext, ToolRegistry

MAX_RESULT_CHARS = 20_000

TurnFn = Callable[..., AsyncIterator[TurnEvent]]


@dataclass
class Text:
    text: str


@dataclass
class ToolStarted:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolFinished:
    id: str
    name: str
    output: str
    is_error: bool = False


@dataclass
class Finished:
    reason: str  # "done" | "step_limit" | "truncated"
    active_skills: list[str] = field(default_factory=list)


AgentEvent = Text | ToolStarted | ToolFinished | Finished


class Agent:
    def __init__(
        self,
        provider: Provider,
        model: str,
        tools: ToolRegistry,
        skills: SkillRegistry,
        gate: PermissionGate | None = None,
        max_steps: int = 20,
        max_tokens: int = 4096,
        turn_fn: TurnFn = stream_turn,
    ) -> None:
        self.provider = provider
        self.model = model
        self.tools = tools
        self.skills = skills
        self.gate = gate or PermissionGate()
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self._turn = turn_fn
        # Skills loaded so far in the current run; the caller saves them with the conversation.
        self.active_skills: set[str] = set()

    async def run(
        self, messages: list[Message], active_skills: set[str] | None = None
    ) -> AsyncIterator[AgentEvent]:
        ctx = RunContext(skills=self.skills, active_skills=set(active_skills or ()))
        self.active_skills = ctx.active_skills
        catalog = self.skills.catalog_prompt()
        for _ in range(self.max_steps):
            # Rebuilt each step: loading a skill unlocks more tools.
            specs = [t.spec() for t in self.tools.available(ctx.granted_tools())]
            system = build_system_prompt(catalog)
            done = TurnDone()
            async for event in self._turn(
                self.provider, self.model, messages, specs, system, self.max_tokens
            ):
                if isinstance(event, TextDelta):
                    yield Text(event.text)
                else:
                    done = event
            messages.append(
                Message(role="assistant", content=done.text, tool_calls=done.tool_calls)
            )
            if not done.tool_calls:
                reason = "truncated" if done.truncated else "done"
                yield Finished(reason, sorted(ctx.active_skills))
                return
            for call in done.tool_calls:
                yield ToolStarted(call.id, call.name, call.arguments)
                output, is_error = await self._execute(call, ctx)
                messages.append(
                    Message(role="tool", content=output, tool_call_id=call.id, is_error=is_error)
                )
                yield ToolFinished(call.id, call.name, output, is_error)
        yield Finished("step_limit", sorted(ctx.active_skills))

    async def _execute(self, call: ToolCall, ctx: RunContext) -> tuple[str, bool]:
        """Run one tool call. Failures go back to the model as error results, never raised."""
        tool = self.tools.get(call.name)
        # Unknown and not-granted look the same to the model: it can only use what it was shown.
        if tool is None or not (tool.core or tool.name in ctx.granted_tools()):
            return f"Unknown tool {call.name!r}. Load the skill that provides it first.", True
        if call.parse_error:
            return f"Invalid arguments for {call.name}: {call.parse_error}.", True
        if missing := tool.missing_args(call.arguments):
            return f"Missing required argument(s) for {call.name}: {', '.join(missing)}.", True
        decision = await self.gate.authorize(tool, call.arguments)
        if not decision.allowed:
            return decision.reason, True
        try:
            output = await tool.handler(call.arguments, ctx)
        except Exception as e:
            return f"{call.name} failed: {e}", True
        if len(output) > MAX_RESULT_CHARS:
            output = output[:MAX_RESULT_CHARS] + f"\n[truncated, {len(output)} characters total]"
        return output, False
