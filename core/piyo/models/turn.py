"""One model turn with tool calling, normalised across the two wire formats.

The agent loop talks to `stream_turn` only. It yields text deltas as they arrive and ends with a
`TurnDone` carrying the full text and any tool calls the model asked for.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from piyo.models.clients import _client
from piyo.models.providers import ApiStyle, Provider


class ToolSpec(BaseModel):
    """What the model is told about a tool. `name` must already be wire-safe."""

    name: str
    description: str
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict = Field(default_factory=dict)
    # Set when the model sent arguments that weren't valid JSON; the loop reports it back.
    parse_error: str | None = None


class Message(BaseModel):
    """Conversation entry for the agent loop (richer than the plain-text `ChatMessage`)."""

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)  # assistant only
    tool_call_id: str | None = None  # tool only
    is_error: bool = False  # tool only


@dataclass
class TextDelta:
    text: str


@dataclass
class TurnDone:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    truncated: bool = False  # hit max_tokens


TurnEvent = TextDelta | TurnDone


def _parse_args(raw: str) -> tuple[dict, str | None]:
    if not raw.strip():
        return {}, None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        return {}, f"arguments were not valid JSON ({e.msg})"
    if not isinstance(value, dict):
        return {}, "arguments must be a JSON object"
    return value, None


# --- Anthropic wire format -------------------------------------------------------------------


def to_anthropic_messages(messages: list[Message]) -> list[dict]:
    wire: list[dict] = []
    for m in messages:
        if m.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m.tool_call_id,
                "content": m.content,
                "is_error": m.is_error,
            }
            # Results for one assistant turn must share a single user message.
            if wire and wire[-1]["role"] == "user" and isinstance(wire[-1]["content"], list) and (
                wire[-1]["content"][-1]["type"] == "tool_result"
            ):
                wire[-1]["content"].append(block)
            else:
                wire.append({"role": "user", "content": [block]})
        elif m.role == "assistant" and m.tool_calls:
            blocks: list[dict] = [{"type": "text", "text": m.content}] if m.content else []
            blocks += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in m.tool_calls
            ]
            wire.append({"role": "assistant", "content": blocks})
        else:
            wire.append({"role": m.role, "content": m.content})
    return wire


def to_anthropic_tools(tools: list[ToolSpec]) -> list[dict]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
    ]


# --- OpenAI-compatible wire format -----------------------------------------------------------


def to_openai_messages(messages: list[Message], system: str | None = None) -> list[dict]:
    wire: list[dict] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        if m.role == "tool":
            wire.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == "assistant" and m.tool_calls:
            wire.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in m.tool_calls
                    ],
                }
            )
        else:
            wire.append({"role": m.role, "content": m.content})
    return wire


def to_openai_tools(tools: list[ToolSpec]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]


# --- Streaming -------------------------------------------------------------------------------


async def stream_turn(
    provider: Provider,
    model: str,
    messages: list[Message],
    tools: list[ToolSpec],
    system: str | None = None,
    max_tokens: int = 4096,
) -> AsyncIterator[TurnEvent]:
    if provider.api_style is ApiStyle.ANTHROPIC:
        async for event in _stream_anthropic(provider, model, messages, tools, system, max_tokens):
            yield event
    else:
        async for event in _stream_openai(provider, model, messages, tools, system, max_tokens):
            yield event


async def _stream_anthropic(provider, model, messages, tools, system, max_tokens):
    client = _client(provider)
    kwargs: dict = {}
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = to_anthropic_tools(tools)
    async with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        messages=to_anthropic_messages(messages),
        **kwargs,
    ) as stream:
        async for text in stream.text_stream:
            yield TextDelta(text)
        final = await stream.get_final_message()
    yield TurnDone(
        text="".join(b.text for b in final.content if b.type == "text"),
        tool_calls=[
            ToolCall(id=b.id, name=b.name, arguments=b.input or {})
            for b in final.content
            if b.type == "tool_use"
        ],
        truncated=final.stop_reason == "max_tokens",
    )


async def _stream_openai(provider, model, messages, tools, system, max_tokens):
    client = _client(provider)
    kwargs: dict = {"tools": to_openai_tools(tools)} if tools else {}
    stream = await client.chat.completions.create(
        model=model,
        messages=to_openai_messages(messages, system),
        max_tokens=max_tokens,
        stream=True,
        **kwargs,
    )
    text: list[str] = []
    partial: dict[int, dict] = {}  # tool call index -> {id, name, args}
    finish: str | None = None
    async for chunk in stream:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta
        if delta.content:
            text.append(delta.content)
            yield TextDelta(delta.content)
        for tc in delta.tool_calls or []:
            slot = partial.setdefault(tc.index, {"id": "", "name": "", "args": ""})
            slot["id"] = tc.id or slot["id"]
            if tc.function:
                slot["name"] += tc.function.name or ""
                slot["args"] += tc.function.arguments or ""
        finish = choice.finish_reason or finish

    calls = []
    for index in sorted(partial):
        slot = partial[index]
        args, error = _parse_args(slot["args"])
        calls.append(
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=args,
                parse_error=error,
            )
        )
    yield TurnDone(text="".join(text), tool_calls=calls, truncated=finish == "length")
