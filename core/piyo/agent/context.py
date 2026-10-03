"""Keep what is sent to the model inside its context window.

`fit_context` returns a trimmed copy for one model turn; the stored conversation is never changed, so
nothing is lost on disk. Cheapest cuts first: old tool output, then whole old turns, then (only when
the current turn alone is too big) its earlier tool output. A turn starts at a user message and
tool calls stay together with their results, so providers never see an unanswered call.
"""

from __future__ import annotations

import json

from piyo.models.turn import Message, ToolSpec

# Short results (errors, "sent") are cheaper to keep than to replace.
KEEP_CHARS = 200
IMAGE_TOKENS = 1500  # a screenshot costs about this much input, whatever its bytes


def estimate_tokens(text: str) -> int:
    # Rough on purpose: 3 characters per token errs on the safe side for English and code.
    return len(text) // 3 + 1


def message_tokens(m: Message) -> int:
    calls = sum(estimate_tokens(c.name + json.dumps(c.arguments)) for c in m.tool_calls)
    return estimate_tokens(m.content) + calls + len(m.images) * IMAGE_TOKENS + 4


def prompt_overhead(system: str, specs: list[ToolSpec]) -> int:
    return estimate_tokens(system) + sum(estimate_tokens(s.model_dump_json()) for s in specs)


def _trimmed(m: Message) -> Message:
    note = f"[older tool output removed to save space, {len(m.content)} characters]"
    return m.model_copy(update={"content": note, "images": []})


def fit_context(messages: list[Message], budget: int) -> list[Message]:
    """Return `messages`, or a shortened copy that fits `budget` tokens when possible."""
    out = list(messages)
    total = sum(message_tokens(m) for m in out)
    if total <= budget:
        return out

    def shrink(indices: list[int]) -> None:
        nonlocal total
        for i in indices:
            if total <= budget:
                return
            m = out[i]
            if m.role == "tool" and (len(m.content) > KEEP_CHARS or m.images):
                new = _trimmed(m)
                total -= message_tokens(m) - message_tokens(new)
                out[i] = new

    def current_turn_start() -> int:
        return max((i for i, m in enumerate(out) if m.role == "user"), default=0)

    shrink(list(range(current_turn_start())))

    # Drop whole old turns, oldest first. The current turn is never dropped.
    while total > budget:
        starts = [i for i, m in enumerate(out) if m.role == "user"]
        if len(starts) < 2:
            break
        cut = starts[1]
        total -= sum(message_tokens(m) for m in out[:cut])
        del out[:cut]

    # The current turn alone is too big: trim its tool output too, keeping the latest results.
    start = current_turn_start()
    last_assistant = max(
        (i for i in range(start, len(out)) if out[i].role == "assistant"), default=len(out)
    )
    shrink([i for i in range(start, last_assistant) if out[i].role == "tool"])
    return out
