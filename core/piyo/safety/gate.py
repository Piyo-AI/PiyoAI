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


# Asks the user (via the app) and returns their answer.
Approver = Callable[[ApprovalRequest], Awaitable[bool]]


@dataclass
class GateResult:
    allowed: bool
    reason: str = ""


class PermissionGate:
    def __init__(self, approver: Approver | None = None) -> None:
        self._approver = approver

    async def authorize(self, tool: Tool, arguments: dict) -> GateResult:
        risk = tool.risk_of(arguments)
        if risk is Risk.AUTO:
            return GateResult(True)
        if risk is Risk.NEVER:
            return GateResult(
                False, "This action is never automated; ask the user to do it themselves."
            )
        if self._approver is None:
            return GateResult(False, "This action needs the user's approval and none is available.")
        if await self._approver(ApprovalRequest(tool.name, arguments, risk)):
            return GateResult(True)
        return GateResult(False, "The user declined this action.")
