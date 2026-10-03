"""Tools are primitive capabilities the agent can call.

Names use dots for grouping (`gmail.read`); the wire name swaps them for `__` because providers only
accept `[a-zA-Z0-9_-]`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from piyo.models.capabilities import ModelCaps
from piyo.models.turn import Image, ToolSpec

if TYPE_CHECKING:
    from piyo.skills import SkillRegistry


class Risk(StrEnum):
    AUTO = "auto"  # read, search, summarize, draft
    CONFIRM = "confirm"  # send, modify, submit, purchase, delete: the user approves each call
    NEVER = "never"  # never automated, handed back to the user


@dataclass
class RunContext:
    """Per-run state shared by the loop and the tools."""

    skills: SkillRegistry
    active_skills: set[str] = field(default_factory=set)
    model_caps: ModelCaps | None = None  # what the selected model can do; None skips the check
    # Says why an integration a skill needs isn't usable (not connected, expired), None when it is.
    integration_issue: Callable[[str], str | None] | None = None

    # Pictures tools attached during the current call; the loop moves them onto the tool result message.
    pending_images: list[Image] = field(default_factory=list)

    def attach_image(self, data: str, media_type: str = "image/jpeg") -> None:
        self.pending_images.append(Image(media_type=media_type, data=data))

    def take_images(self) -> list[Image]:
        images, self.pending_images = self.pending_images, []
        return images

    def granted_tools(self) -> set[str]:
        """Tools unlocked by the skills loaded so far (union of their `requires.tools`)."""
        granted: set[str] = set()
        for name in self.active_skills:
            skill = self.skills.get(name)
            if skill:
                granted.update(skill.manifest.requires.tools)
        return granted


Handler = Callable[[dict, RunContext], Awaitable[str]]


@dataclass
class Tool:
    name: str
    description: str
    handler: Handler
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    risk: Risk = Risk.AUTO
    # Lets a tool's own code relax `risk` for a specific call (for example inside a folder the user
    # trusted). Set by tool authors only; a skill can't reach it. Errors fall back to `risk`.
    risk_for: Callable[[dict], Risk] | None = None
    # One plain sentence saying what a call will do, built from its real arguments by the tool's own
    # code (never from model text), shown on the approval card. Errors fall back to a generic line.
    summarize: Callable[[dict], str] | None = None
    # Core tools are always available; the rest must be granted by a loaded skill.
    core: bool = False

    @property
    def wire_name(self) -> str:
        return self.name.replace(".", "__")

    def risk_of(self, args: dict) -> Risk:
        if self.risk_for is None:
            return self.risk
        try:
            return self.risk_for(args)
        except Exception:
            return self.risk

    def summary_of(self, args: dict) -> str:
        if self.summarize is not None:
            try:
                return self.summarize(args)
            except Exception:
                pass
        shown = ", ".join(f"{k}: {v}" for k, v in args.items())
        return f"Run {self.name}" + (f" with {shown[:200]}" if shown else "")

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.wire_name, description=self.description, parameters=self.parameters
        )

    def missing_args(self, args: dict) -> list[str]:
        return [k for k in self.parameters.get("required", []) if k not in args]


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Look up by internal or wire name."""
        return self._tools.get(name) or self._tools.get(name.replace("__", "."))

    def names(self) -> set[str]:
        return set(self._tools)

    def available(self, granted: set[str]) -> list[Tool]:
        return [t for t in self._tools.values() if t.core or t.name in granted]
