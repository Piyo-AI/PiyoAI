"""Permission gate: every tool call passes through here before it runs.

Risk is a property of the tool, not of any skill, so a `SKILL.md` can't talk its way past a
confirmation. Tools outside the granted set never reach this point (the loop rejects them first).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from piyo.tools.base import Risk, Tool


@dataclass
class ApprovalRequest:
    tool: str
    arguments: dict
    risk: Risk
    summary: str = ""  # built by the tool from the real arguments
    why: str = ""  # the model's own words for this turn; shown as its claim, not as fact


# Asks the user (via the app) and returns their answer.
Approver = Callable[[ApprovalRequest], Awaitable[bool]]


class ApprovalDeferred(Exception):
    """Raised by an approver that cannot ask the user right now (a scheduled run). The call does not run; the
    message goes back to the model, and the user decides later from the pending list."""


@dataclass
class GateResult:
    allowed: bool
    reason: str = ""
    declined: bool = False  # the user said no (as opposed to "never" or "no approver")


class PermissionGate:
    def __init__(self, approver: Approver | None = None) -> None:
        self._approver = approver

    async def authorize(self, tool: Tool, arguments: dict, why: str = "") -> GateResult:
        risk = tool.risk_of(arguments)
        if risk is Risk.AUTO:
            return GateResult(True)
        if risk is Risk.NEVER:
            return GateResult(
                False, "This action is never automated; ask the user to do it themselves."
            )
        if self._approver is None:
            return GateResult(False, "This action needs the user's approval and none is available.")
        request = ApprovalRequest(tool.name, arguments, risk, tool.summary_of(arguments), why)
        try:
            approved = await self._approver(request)
        except ApprovalDeferred as held:
            return GateResult(False, str(held), declined=True)  # declined: the loop never retries it
        if approved:
            return GateResult(True)
        return GateResult(False, "The user declined this action.", declined=True)
