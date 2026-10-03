"""Finds skills on disk and exposes them to the agent with progressive loading.

The agent's prompt only carries name + description per skill. The full instructions load on demand
through the `load_skill` tool.
"""

from __future__ import annotations

from pathlib import Path

from piyo.config import skills_dir
from piyo.skills.manifest import Skill, SkillError, load_skill_dir


def default_builtin_dir() -> Path:
    # Repo checkout: <repo>/skills next to <repo>/core. Packaged builds will bundle it instead.
    return Path(__file__).resolve().parents[3] / "skills"


class SkillRegistry:
    def __init__(
        self,
        builtin_dir: Path | None = None,
        user_dir: Path | None = None,
        disabled: set[str] | None = None,
    ) -> None:
        self.builtin_dir = builtin_dir if builtin_dir is not None else default_builtin_dir()
        self.user_dir = user_dir if user_dir is not None else skills_dir()
        self.disabled: set[str] = set(disabled or ())
        self._skills: dict[str, Skill] = {}
        self.errors: dict[str, str] = {}  # folder name -> why it didn't load
        self.reload()

    def reload(self) -> None:
        self._skills.clear()
        self.errors.clear()
        # Built-ins load first and win name clashes, so an installed skill can't shadow one.
        for source, root in (("builtin", self.builtin_dir), ("user", self.user_dir)):
            if not root.is_dir():
                continue
            folders = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
            for path in sorted(folders):
                try:
                    skill = load_skill_dir(path, source)
                except (SkillError, OSError, UnicodeDecodeError) as e:
                    self.errors[f"{source}/{path.name}"] = str(e)
                    continue
                name = skill.manifest.name
                if name in self._skills:
                    self.errors[f"{source}/{path.name}"] = (
                        f"name {name!r} is already used by a {self._skills[name].source} skill"
                    )
                    continue
                self._skills[name] = skill

    def get(self, name: str) -> Skill | None:
        skill = self._skills.get(name)
        return None if skill is None or name in self.disabled else skill

    def list(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.manifest.name)

    def enabled(self) -> list[Skill]:
        return [s for s in self.list() if s.manifest.name not in self.disabled]

    def catalog_prompt(self) -> str:
        """The always-in-context part: one line per enabled skill."""
        skills = self.enabled()
        if not skills:
            return ""
        lines = [f"- {s.manifest.name}: {s.manifest.description}" for s in skills]
        return (
            "Skills you can use. When a request matches one, call load_skill with its name "
            "and follow its instructions:\n" + "\n".join(lines)
        )
