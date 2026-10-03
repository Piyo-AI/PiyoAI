"""Optional smoke test against a real local Ollama, so the local path does not rot.

Skipped unless PIYO_OLLAMA_MODEL names a model that is already pulled, e.g.
    PIYO_OLLAMA_MODEL=qwen2.5:0.5b uv run pytest tests/test_ollama_smoke.py
Assertions are loose on purpose: small models are unpredictable, so this checks the plumbing only.
"""

from __future__ import annotations

import os

import pytest

from piyo.models import ChatMessage, list_models, stream_chat
from piyo.models.prompt_tools import prompt_turn
from piyo.models.providers import ProviderRegistry
from piyo.models.turn import Message, ToolSpec, TurnDone, stream_turn

MODEL = os.environ.get("PIYO_OLLAMA_MODEL")

pytestmark = pytest.mark.skipif(not MODEL, reason="set PIYO_OLLAMA_MODEL to run against a local Ollama")


@pytest.fixture
def ollama():
    return ProviderRegistry().get("ollama")


async def test_model_list_includes_the_model(ollama):
    ids = [m.id for m in await list_models(ollama)]
    assert any(i == MODEL or i.startswith(f"{MODEL}:") for i in ids), ids


async def test_chat_streams_text(ollama):
    reply = "".join(
        [d async for d in stream_chat(ollama, MODEL, [ChatMessage(role="user", content="Say hi.")])]
    )
    assert reply.strip()


async def test_a_tool_turn_completes_through_the_text_protocol(ollama):
    clock = ToolSpec(name="clock__now", description="Get the current time.")
    messages = [Message(role="user", content="What time is it? Use the clock tool.")]
    events = [e async for e in prompt_turn(stream_turn, ollama, MODEL, messages, [clock], None, 512)]
    done = events[-1]
    assert isinstance(done, TurnDone)
    assert done.text.strip() or done.tool_calls
