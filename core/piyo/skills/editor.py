"""Writing and editing the user's own skills, with a history to roll back to (PLAN.md section 5).

Only `SKILL.md` and `SETUP.md` are edited here; a skill's scripts stay as installed. Every save keeps the previous
version first. A save that adds a permission the skill did not have needs that permission approved, the same
rule as an update from a file: whoever sees the editor sees what the skill will be allowed to do.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from piyo.skills.install import (
    BACKUP_FILE,
    META_FILE,
    InstallError,
    _approved,
    backup_skill,
    permissions_of,
    read_meta,
)
from piyo.skills.manifest import Skill, SkillError, load_skill_dir, parse_skill_md

MAX_TEXT = 100_000
KEEP_VERSIONS = 30

TEMPLATE = """\
---
name: {name}
version: 0.1.0
description: "One or two sentences: what this skill does, and when Piyo should use it."
license: MIT
requires:
  tools: []
---

# {title}

1. Step one.
2. Step two.
"""


@dataclass
class Check:
    ok: bool
    error: str | None = None
    name: str | None = None
    version: str | None = None
    permissions: list[str] | None = None
    added: list[str] | None = None  # permissions that need approving to save


@dataclass
class Version:
    id: str
    version: str
    at: str
    reason: str  # update | edit | rollback


def _text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise InstallError(f"{what} must be text.")
    if len(value) > MAX_TEXT:
        raise InstallError(f"{what} is too long (limit {MAX_TEXT // 1000} KB).")
    return value.replace("\r\n", "\n")


class SkillEditor:
    def __init__(self, user_dir: Path, work_dir: Path, builtin_names: set[str] | None = None) -> None:
        self.user_dir = user_dir
        self.backups = work_dir / "backups"
        self.builtin_names = builtin_names or set()

    def _dir(self, name: str) -> Path:
        if "/" in name or "\\" in name or name.startswith(".") or name in self.builtin_names:
            raise InstallError("Only skills you installed or wrote can be edited.")
        path = self.user_dir / name
        if not path.is_dir():
            raise InstallError(f"There is no skill called {name!r}.")
        return path

    # -- reading ---------------------------------------------------------------------------

    def read(self, name: str) -> dict:
        path = self._dir(name)
        setup = path / "SETUP.md"
        return {
            "skill_md": (path / "SKILL.md").read_text(encoding="utf-8"),
            "setup_md": setup.read_text(encoding="utf-8") if setup.is_file() else "",
        }

    def check(self, skill_md: str, name: str | None = None) -> Check:
        """Live validation for the editor. `name` is the skill being edited (its name cannot change)."""
        try:
            manifest, _ = parse_skill_md(_text(skill_md, "SKILL.md"))
            if name and manifest.name != name:
                return Check(False, f"The name must stay {name!r} (it is the folder name). Create a new skill to rename.")
            scripts = []
            if name:
                existing = self.user_dir / name / "scripts"
                scripts = sorted(p.name for p in existing.iterdir()) if existing.is_dir() else []
        except (SkillError, InstallError) as e:
            return Check(False, str(e))
        draft = Skill(manifest=manifest, path=self.user_dir / manifest.name, body="", source="user", scripts=scripts)
        perms = permissions_of(draft)
        have = _approved(self.user_dir / manifest.name) if (self.user_dir / manifest.name).is_dir() else []
        return Check(True, None, manifest.name, manifest.version, perms, [p for p in perms if p not in have])

    # -- writing ---------------------------------------------------------------------------

    def _require(self, check: Check, approved: list[str]) -> None:
        if not check.ok:
            raise InstallError(check.error or "This is not a valid skill.")
        missing = [p for p in check.added or [] if p not in approved]
        if missing:
            raise InstallError("These permissions were not approved: " + ", ".join(missing))

    def create(
        self, skill_md: str, setup_md: str, approved: list[str], source: str = "written in Piyo"
    ) -> Skill:
        skill_md, setup_md = _text(skill_md, "SKILL.md"), _text(setup_md, "SETUP.md")
        check = self.check(skill_md)
        if check.ok and (check.name in self.builtin_names or (self.user_dir / check.name).exists()):
            raise InstallError(f"A skill called {check.name!r} already exists.")
        self._require(check, approved)
        name = check.name
        folder = self.user_dir / name
        folder.mkdir(parents=True)
        try:
            self._write(folder, skill_md, setup_md)
            self._write_meta(folder, source, check.permissions)
            return load_skill_dir(folder, "user")
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def save(self, name: str, skill_md: str, setup_md: str, approved: list[str]) -> Skill:
        path = self._dir(name)
        skill_md, setup_md = _text(skill_md, "SKILL.md"), _text(setup_md, "SETUP.md")
        check = self.check(skill_md, name)
        self._require(check, approved)
        backup_skill(path, self.backups, "edit")
        self._prune(name)
        self._write(path, skill_md, setup_md)
        meta = read_meta(path)
        self._write_meta(path, meta.get("source") or "written in Piyo", check.permissions, meta)
        return load_skill_dir(path, "user")

    @staticmethod
    def _write(folder: Path, skill_md: str, setup_md: str) -> None:
        (folder / "SKILL.md").write_text(skill_md, encoding="utf-8", newline="\n")
        setup = folder / "SETUP.md"
        if setup_md.strip():
            setup.write_text(setup_md, encoding="utf-8", newline="\n")
        else:
            setup.unlink(missing_ok=True)

    @staticmethod
    def _write_meta(folder: Path, source: str, permissions: list[str] | None, old: dict | None = None) -> None:
        meta = {**(old or {}), "source": source, "approved": permissions or []}
        meta["edited_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        (folder / META_FILE).write_text(json.dumps(meta), encoding="utf-8")

    # -- history ---------------------------------------------------------------------------

    def history(self, name: str) -> list[Version]:
        self._dir(name)
        root = self.backups / name
        versions = []
        for folder in sorted(root.iterdir(), reverse=True) if root.is_dir() else []:
            try:
                info = json.loads((folder / BACKUP_FILE).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            versions.append(Version(folder.name, str(info.get("version")), str(info.get("at")), str(info.get("reason"))))
        return versions

    def _prune(self, name: str) -> None:
        root = self.backups / name
        for old in sorted(root.iterdir(), reverse=True)[KEEP_VERSIONS:] if root.is_dir() else []:
            shutil.rmtree(old, ignore_errors=True)

    def rollback(self, name: str, version_id: str, approved: list[str]) -> Skill:
        """Go back to a kept version. The current one is kept too, so a rollback can be undone."""
        path = self._dir(name)
        if "/" in version_id or "\\" in version_id or version_id.startswith("."):
            raise InstallError("Unknown version.")
        source = self.backups / name / version_id
        if not (source / BACKUP_FILE).is_file():
            raise InstallError("That version is no longer available.")
        try:
            manifest, body = parse_skill_md((source / "SKILL.md").read_text(encoding="utf-8"))
        except (SkillError, OSError, UnicodeDecodeError) as e:
            raise InstallError(f"That version can not be restored: {e}") from None
        scripts_dir = source / "scripts"
        scripts = sorted(p.name for p in scripts_dir.iterdir()) if scripts_dir.is_dir() else []
        perms = permissions_of(Skill(manifest=manifest, path=source, body=body, source="user", scripts=scripts))
        have = _approved(path)
        missing = [p for p in perms if p not in have and p not in approved]
        if missing:
            raise InstallError("These permissions were not approved: " + ", ".join(missing))
        backup_skill(path, self.backups, "rollback")
        self._prune(name)
        keep_meta = read_meta(path)
        for child in path.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        for child in source.iterdir():
            if child.name in (BACKUP_FILE, META_FILE):
                continue
            shutil.copytree(child, path / child.name) if child.is_dir() else shutil.copy2(child, path / child.name)
        self._write_meta(path, keep_meta.get("source") or "written in Piyo", perms, keep_meta)
        return load_skill_dir(path, "user")
