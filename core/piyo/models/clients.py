"""Thin streaming clients for the two supported wire formats."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic import BaseModel

from piyo.models.providers import ApiStyle, Provider


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class MissingApiKey(RuntimeError):
    pass


def _key(provider: Provider) -> str:
    key = provider.api_key()
    if key:
        return key
    if provider.requires_key:
        raise MissingApiKey(f"No API key set for {provider.name}")
    return "not-needed"  # local servers ignore it, but the SDKs require a value


async def stream_chat(
    provider: Provider,
    model: str,
    messages: list[ChatMessage],
    system: str | None = None,
    max_tokens: int = 4096,
) -> AsyncIterator[str]:
    """Yield text deltas of the assistant reply."""
    if provider.api_style is ApiStyle.ANTHROPIC:
        client = AsyncAnthropic(api_key=_key(provider), base_url=provider.base_url)
        kwargs = {"system": system} if system else {}
        async with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            messages=[m.model_dump() for m in messages],
            **kwargs,
        ) as stream:
            async for text in stream.text_stream:
                yield text
        return

    client = AsyncOpenAI(api_key=_key(provider), base_url=provider.base_url)
    wire = [m.model_dump() for m in messages]
    if system:
        wire.insert(0, {"role": "system", "content": system})
    stream = await client.chat.completions.create(
        model=model, messages=wire, max_tokens=max_tokens, stream=True
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


async def list_models(provider: Provider) -> list[str]:
    """Fetch the provider's live model list (avoids hard-coding model names that go stale)."""
    if provider.api_style is ApiStyle.ANTHROPIC:
        client = AsyncAnthropic(api_key=_key(provider), base_url=provider.base_url)
    else:
        client = AsyncOpenAI(api_key=_key(provider), base_url=provider.base_url)
    ids = [m.id async for m in client.models.list()]
    return sorted(ids)
