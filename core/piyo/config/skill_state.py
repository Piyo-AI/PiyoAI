"""Which skills the user has switched off. Skills are on unless listed here."""

from __future__ import annotations

import json
from pathlib import Path

from piyo.config import data_dir


class SkillState:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or data_dir() / "skills_disabled.json"

    def disabled(self) -> set[str]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return {n for n in raw if isinstance(n, str)} if isinstance(raw, list) else set()

    def set_enabled(self, name: str, enabled: bool) -> set[str]:
        off = self.disabled()
        off.discard(name) if enabled else off.add(name)
        self.path.write_text(json.dumps(sorted(off)), encoding="utf-8")
        return off
