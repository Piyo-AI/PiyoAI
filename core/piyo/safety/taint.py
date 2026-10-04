"""Keeping text from outside from writing into long-term memory unnoticed.

`memory.remember` runs without asking, because remembering what the user just said is the point. But a web
page, e-mail or file the model has read could steer it to store a false "fact" (a "preferred" payment address,
a "known" contact) that is recalled in every later run. The fence around outside text makes that unlikely,
not impossible, and a stored note outlives the run that wrote it.

So a run becomes *tainted* once a tool that returns text written by someone else has run (or the chat
already holds such a result from an earlier turn). From then on `memory.remember` asks first and the card
says why. Untainted runs are not affected. Mirrors `safety/exfil.py`, which does the same for private data
and outbound strings. What this does not cover is listed in docs/security-backlog.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from piyo.models.turn import Message

if TYPE_CHECKING:
    from piyo.tools.base import RunContext

# Tools whose output is text somebody else wrote. A new tool that returns outside text goes here
# (tests/test_taint.py fails for a registered tool nobody has classified).
UNTRUSTED_PREFIXES = ("web.", "browser.", "gmail.", "calendar.")
UNTRUSTED_TOOLS = {"files.read", "files.list", "skill.run_script"}


def _normal(name: str) -> str:
    return name.replace("__", ".")


def produces_untrusted(tool_name: str) -> bool:
    name = _normal(tool_name)
    return name in UNTRUSTED_TOOLS or name.startswith(UNTRUSTED_PREFIXES)


def history_has_untrusted(messages: list[Message]) -> bool:
    """An earlier turn of this chat already read outside text (the model still has it in front of it)."""
    failed = {m.tool_call_id for m in messages if m.role == "tool" and m.is_error}
    return any(
        call.id not in failed and produces_untrusted(call.name)
        for m in messages
        for call in m.tool_calls or []
    )


class MemoryGuard:
    """Asks before a note is stored once the run has read outside text. Never makes anything less strict."""

    def reason(self, args: dict, ctx: RunContext) -> str | None:
        if not ctx.untrusted_text:
            return None
        return (
            "This chat has read text from the web, mail or a file, which could be trying to plant a false "
            "note. Only approve it if you want this remembered."
        )

    def remember(self, args: dict, ctx: RunContext) -> None:
        """Nothing to track: every note in a tainted run asks."""
