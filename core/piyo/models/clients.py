"""Thin streaming clients for the two supported wire formats."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

import anthropic
import httpx
import openai
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic import BaseModel

from piyo.models.providers import ApiStyle, Provider


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class MissingApiKey(RuntimeError):
    pass


class ModelInfo(BaseModel):
    id: str
    # None means the provider doesn't report it (most don't).
    free: bool | None = None
    context_length: int | None = None
    tools: bool | None = None


def _is_zero(value: object) -> bool:
    try:
        return float(value) == 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def parse_openrouter_models(payload: dict) -> list[ModelInfo]:
    """OpenRouter's /models reports pricing and supported parameters for every model."""
    models = []
    for m in payload.get("data", []):
        pricing = m.get("pricing")
        free = None
        if pricing is not None:
            free = m["id"].endswith(":free") or (
                _is_zero(pricing.get("prompt")) and _is_zero(pricing.get("completion"))
            )
        params = m.get("supported_parameters")
        models.append(
            ModelInfo(
                id=m["id"],
                free=free,
                context_length=m.get("context_length"),
                tools=None if params is None else "tools" in params,
            )
        )
    return sorted(models, key=lambda m: m.id)


# Local servers that aren't running should fail in seconds, not minutes.
_TIMEOUT = httpx.Timeout(120.0, connect=5.0)
_MAX_RETRIES = 1


def _client(provider: Provider) -> AsyncAnthropic | AsyncOpenAI:
    kwargs = {
        "api_key": _key(provider),
        "base_url": provider.base_url,
        "timeout": _TIMEOUT,
        "max_retries": _MAX_RETRIES,
    }
    if provider.api_style is ApiStyle.ANTHROPIC:
        return AsyncAnthropic(**kwargs)
    return AsyncOpenAI(**kwargs)


def describe_error(provider: Provider, exc: Exception) -> str:
    """Turn SDK exceptions into messages a user can act on."""
    if isinstance(exc, MissingApiKey):
        return str(exc)
    if isinstance(exc, openai.APIConnectionError | anthropic.APIConnectionError):
        hint = " Is it running?" if provider.local else " Check your internet connection."
        return f"Couldn't connect to {provider.name} at {provider.base_url}.{hint}"
    if isinstance(exc, openai.AuthenticationError | anthropic.AuthenticationError):
        return f"{provider.name} rejected the API key. Check it in Settings."
    if isinstance(exc, openai.NotFoundError | anthropic.NotFoundError):
        return f"{provider.name} doesn't know that model. Pick another one."
    if isinstance(exc, openai.RateLimitError | anthropic.RateLimitError):
        return f"{provider.name} is rate limiting requests. Try again in a moment."
    return f"{provider.name}: {exc}"


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
        client = _client(provider)
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

    client = _client(provider)
    wire = [m.model_dump() for m in messages]
    if system:
        wire.insert(0, {"role": "system", "content": system})
    stream = await client.chat.completions.create(
        model=model, messages=wire, max_tokens=max_tokens, stream=True
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


async def list_models(provider: Provider) -> list[ModelInfo]:
    """Fetch the provider's live model list (avoids hard-coding model names that go stale)."""
    if "openrouter.ai" in provider.base_url:
        # Public endpoint; the key is optional here but sent when we have one.
        headers = {"Authorization": f"Bearer {key}"} if (key := provider.api_key()) else {}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            res = await http.get(f"{provider.base_url.rstrip('/')}/models", headers=headers)
            res.raise_for_status()
            return parse_openrouter_models(res.json())
    client = _client(provider)
    models = [ModelInfo(id=m.id) async for m in client.models.list()]
    return sorted(models, key=lambda m: m.id)
