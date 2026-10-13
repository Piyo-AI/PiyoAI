"""What a background agent may use: its scope.

The user sets an agent's scope (in the app, or by approving `agent.create`); nothing a skill, page or model
says can widen it. While an agent runs, its scope sits in a context variable, so the tools that touch a scoped
resource check it themselves: file tools (folders), Google (accounts), the browser (mode) and `load_skill`
(skills, through a restricted `SkillRegistry`). asyncio copies the context into each task, so concurrent runs
never see each other's scope. Outside an agent run (the chat) there is no scope and nothing changes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

BROWSER_MODES = ("fresh", "profile", "off")
MAX_ITEMS = 50


class ScopeError(ValueError):
    pass


@dataclass(frozen=True)
class Scope:
    # None means "any": every enabled skill, every folder the user approved, every connected Google account.
    skills: tuple[str, ...] | None = None
    folders: tuple[str, ...] | None = None
    accounts: tuple[str, ...] | None = None
    # fresh: a clean browser context (not built yet, so browser tools refuse); profile: the signed-in profile.
    browser: str = "profile"
    # The agent's own folder, set for a run (not stored): relative paths land here and it is always writable.
    home: Path | None = field(default=None, compare=False)

    def to_json(self) -> dict:
        return {
            "skills": list(self.skills) if self.skills is not None else None,
            "folders": list(self.folders) if self.folders is not None else None,
            "accounts": list(self.accounts) if self.accounts is not None else None,
            "browser": self.browser,
        }

    def describe(self) -> str:
        """One line for an approval card or a listing."""

        def some(items: tuple[str, ...] | None, every: str) -> str:
            if items is None:
                return every
            return ", ".join(items) if items else "none"

        browser = {
            "fresh": "a clean browser",
            "profile": "your signed-in browser profile",
            "off": "no browser",
        }[self.browser]
        return (
            f"skills: {some(self.skills, 'any enabled')}; folders: its own"
            + (f" + {some(self.folders, 'all approved')}" if self.folders != () else "")
            + f"; Google accounts: {some(self.accounts, 'any connected')}; {browser}"
        )


# Agents made before scopes existed (and `schedule.create`) keep what scheduled runs could always do.
LEGACY = Scope()
# A new agent: its own folder only, a clean browser; skills and accounts as the user chooses.
NEW_AGENT = Scope(folders=(), browser="fresh")


def _names(raw: object, what: str, lower: bool = False) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ScopeError(f"{what} must be a list of names, or null for any.")
    clean = [x.strip().lower() if lower else x.strip() for x in raw]
    clean = list(dict.fromkeys(x for x in clean if x))
    if len(clean) > MAX_ITEMS:
        raise ScopeError(f"Too many {what} (limit {MAX_ITEMS}).")
    return tuple(clean)


def parse_scope(raw: object, default: Scope = NEW_AGENT) -> Scope:
    """A scope from JSON; missing keys take `default`'s value."""
    if raw is None:
        return default
    if not isinstance(raw, dict):
        raise ScopeError("The scope must be an object.")
    if bad := set(raw) - {"skills", "folders", "accounts", "browser"}:
        raise ScopeError(f"Unknown scope field(s): {', '.join(sorted(bad))}.")
    browser = raw.get("browser", default.browser)
    if browser not in BROWSER_MODES:
        raise ScopeError(f"browser must be one of {', '.join(BROWSER_MODES)}.")
    folders = _names(raw["folders"], "folders") if "folders" in raw else default.folders
    for f in folders or ():
        if not Path(f).expanduser().is_absolute():
            raise ScopeError(f"Use a full folder path, not {f!r}.")
    return Scope(
        skills=_names(raw["skills"], "skills") if "skills" in raw else default.skills,
        folders=folders,
        accounts=_names(raw["accounts"], "accounts", lower=True) if "accounts" in raw else default.accounts,
        browser=browser,
    )


_current: ContextVar[Scope | None] = ContextVar("piyo_scope", default=None)


def current_scope() -> Scope | None:
    """The scope of the agent run this code is part of; None in the chat."""
    return _current.get()


@contextmanager
def use_scope(scope: Scope | None) -> Iterator[None]:
    token = _current.set(scope)
    try:
        yield
    finally:
        _current.reset(token)
