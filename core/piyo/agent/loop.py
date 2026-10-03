"""The agent loop: model turn -> tool calls -> results -> repeat until the model is done.

`Agent.run` appends to the caller's message list, so the caller keeps the conversation state.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from piyo.agent.context import estimate_tokens, fit_context, message_tokens, prompt_overhead
from piyo.agent.prompts import build_system_prompt
from piyo.models.capabilities import ModelCaps
from piyo.models.providers import Provider
from piyo.models.turn import (
    Message,
    TextDelta,
    ToolCall,
    ToolSpec,
    TurnDone,
    TurnEvent,
    stream_turn,
)
from piyo.safety import PermissionGate
from piyo.safety.untrusted import shorten
from piyo.skills import SkillRegistry
from piyo.store import RunLog
from piyo.tools import RunContext, ToolRegistry

MAX_RESULT_CHARS = 20_000
MAX_IDENTICAL_FAILURES = 3  # the same call with the same arguments may fail this often per run

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
    reason: str  # "done" | "step_limit" | "token_limit" | "timeout" | "truncated"
    active_skills: list[str] = field(default_factory=list)


AgentEvent = Text | ToolStarted | ToolFinished | Finished


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


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
        context_tokens: int | None = None,  # the model's window; None sends the history untrimmed
        log: RunLog | None = None,  # the task log for this run, filled in as it goes
        model_caps: ModelCaps | None = None,  # what the model can do; None skips skill checks
        max_total_tokens: int | None = None,  # budget: input + output over the whole run
        timeout_s: float | None = None,  # budget: wall clock, minus time waiting for approvals
    ) -> None:
        self.model_caps = model_caps
        self.max_total_tokens = max_total_tokens
        self.timeout_s = timeout_s
        self.tokens_used = 0
        self._approval_wait = 0.0
        self._failures: dict[str, int] = {}  # identical failing calls this run
        self._declined: set[str] = set()  # calls the user said no to this run
        self.log = log
        self.context_tokens = context_tokens
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
        ctx = RunContext(
            skills=self.skills, active_skills=set(active_skills or ()), model_caps=self.model_caps
        )
        self.active_skills = ctx.active_skills
        catalog = self.skills.catalog_prompt(self.model_caps)
        started_run = time.monotonic()
        self._approval_wait = 0.0
        self.tokens_used = 0
        self._failures, self._declined = {}, set()
        for _ in range(self.max_steps):
            # Checked between model turns, so a tool call is never left without its result.
            if self.max_total_tokens and self.tokens_used >= self.max_total_tokens:
                yield Finished("token_limit", sorted(ctx.active_skills))
                return
            elapsed = time.monotonic() - started_run - self._approval_wait
            if self.timeout_s and elapsed > self.timeout_s:
                yield Finished("timeout", sorted(ctx.active_skills))
                return
            # Rebuilt each step: loading a skill unlocks more tools.
            specs = [t.spec() for t in self.tools.available(ctx.granted_tools())]
            system = build_system_prompt(catalog)
            done = TurnDone()
            sent = self._fit(messages, specs, system)
            started = time.monotonic()
            async for event in self._turn(
                self.provider, self.model, sent, specs, system, self.max_tokens
            ):
                if isinstance(event, TextDelta):
                    yield Text(event.text)
                else:
                    done = event
            self._log_turn(done, sent, specs, system, started)
            messages.append(
                Message(role="assistant", content=done.text, tool_calls=done.tool_calls)
            )
            if not done.tool_calls:
                reason = "truncated" if done.truncated else "done"
                yield Finished(reason, sorted(ctx.active_skills))
                return
            for call in done.tool_calls:
                yield ToolStarted(call.id, call.name, call.arguments)
                started = time.monotonic()
                before = set(ctx.active_skills)
                output, is_error = await self._execute(call, ctx, done.text)
                if self.log:
                    self.log.step(
                        "tool",
                        _ms(started),
                        name=call.name,
                        arguments=call.arguments,
                        output=output,
                        is_error=is_error,
                    )
                if loaded := sorted(ctx.active_skills - before):
                    # New tools are unlocked, so earlier "unknown tool" failures no longer count.
                    self._failures.clear()
                    if self.log:
                        for name in loaded:
                            self.log.step("skill", name=name)
                messages.append(
                    Message(role="tool", content=output, tool_call_id=call.id, is_error=is_error)
                )
                yield ToolFinished(call.id, call.name, output, is_error)
        yield Finished("step_limit", sorted(ctx.active_skills))

    def _log_turn(self, done: TurnDone, sent, specs, system, started: float) -> None:
        estimated = done.input_tokens is None or done.output_tokens is None
        if estimated:  # the provider did not report usage
            input_tokens = prompt_overhead(system, specs) + sum(message_tokens(m) for m in sent)
            output_tokens = estimate_tokens(done.text) + sum(
                estimate_tokens(c.name + str(c.arguments)) for c in done.tool_calls
            )
        else:
            input_tokens, output_tokens = done.input_tokens, done.output_tokens
        self.tokens_used += input_tokens + output_tokens
        if not self.log:
            return
        self.log.add_tokens(input_tokens, output_tokens, estimated)
        self.log.step(
            "model",
            _ms(started),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated=estimated,
            messages_sent=len(sent),
            text=done.text,
            tool_calls=[c.name for c in done.tool_calls],
            truncated=done.truncated,
        )

    def _fit(self, messages: list[Message], specs: list[ToolSpec], system: str) -> list[Message]:
        """The view of the history sent to the model; `messages` itself stays whole."""
        if self.context_tokens is None:
            return messages
        budget = self.context_tokens - self.max_tokens - prompt_overhead(system, specs)
        return fit_context(messages, max(budget, 0))

    async def _execute(self, call: ToolCall, ctx: RunContext, why: str = "") -> tuple[str, bool]:
        """Run one tool call. Failures go back to the model as error results, never raised.

        Loop guard: an identical call the user declined is not asked about again, and one that
        keeps failing is refused after MAX_IDENTICAL_FAILURES tries, so a confused model can't
        nag the user or burn the budget.
        """
        key = f"{call.name}\0{json.dumps(call.arguments, sort_keys=True, default=str)}"
        if key in self._declined:
            return (
                "The user already declined this exact action in this run. Do not ask again; "
                "continue without it or ask the user what they want instead.",
                True,
            )
        if self._failures.get(key, 0) >= MAX_IDENTICAL_FAILURES:
            return (
                f"{call.name} has failed {MAX_IDENTICAL_FAILURES} times with exactly these "
                "arguments, so it was not run again. Change your approach or tell the user.",
                True,
            )
        output, is_error, declined = await self._execute_once(call, ctx, why)
        if declined:
            self._declined.add(key)
        elif is_error:
            self._failures[key] = self._failures.get(key, 0) + 1
        return output, is_error

    async def _execute_once(
        self, call: ToolCall, ctx: RunContext, why: str
    ) -> tuple[str, bool, bool]:
        """(output, is_error, declined_by_user)"""
        tool = self.tools.get(call.name)
        # Unknown and not-granted look the same to the model: it can only use what it was shown.
        if tool is None or not (tool.core or tool.name in ctx.granted_tools()):
            return f"Unknown tool {call.name!r}. Load the skill that provides it first.", True, False
        if call.parse_error:
            return f"Invalid arguments for {call.name}: {call.parse_error}.", True, False
        if missing := tool.missing_args(call.arguments):
            msg = f"Missing required argument(s) for {call.name}: {', '.join(missing)}."
            return msg, True, False
        asked = time.monotonic()
        decision = await self.gate.authorize(tool, call.arguments, why)
        self._approval_wait += time.monotonic() - asked  # the user's thinking time isn't the run's
        if not decision.allowed:
            return decision.reason, True, decision.declined
        try:
            output = await tool.handler(call.arguments, ctx)
        except Exception as e:
            return f"{call.name} failed: {e}", True, False
        return shorten(output, MAX_RESULT_CHARS), False, False
