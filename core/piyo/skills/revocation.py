"""What to do when the catalog withdraws a skill version the user has installed (PLAN.md section 5).

`check_catalog` compares the installed skills that came from the catalog with a fresh index. A withdrawn
version is switched off once, and the user is told until they acknowledge it. It is switched off, not deleted:
the user can read why, turn it back on if they disagree (we do not switch it off a second time for the
same version), or uninstall it. Skills installed from a file or an arbitrary Git address are never touched,
since the catalog has no say over them. Updates are only reported here; installing one still goes through
the permission review.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from piyo.config import data_dir
from piyo.skills.catalog import Catalog
from piyo.skills.install import read_meta
from piyo.skills.manifest import Skill


@dataclass
class Withdrawn:
    name: str
    version: str
    reason: str
    acknowledged: bool = False


@dataclass
class Update:
    name: str
    version: str
    installed_version: str
    adds_permissions: list[str] = field(default_factory=list)  # asked for now and not approved before


class RevocationStore:
    """Withdrawn versions we have already acted on, in `skills_revoked.json` (name -> record)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or data_dir() / "skills_revoked.json"

    def all(self) -> dict[str, Withdrawn]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        found: dict[str, Withdrawn] = {}
        for name, rec in raw.items() if isinstance(raw, dict) else []:
            if (
                isinstance(rec, dict)
                and isinstance(rec.get("version"), str)
                and isinstance(rec.get("reason"), str)
            ):
                found[name] = Withdrawn(name, rec["version"], rec["reason"], rec.get("acknowledged") is True)
        return found

    def save(self, records: dict[str, Withdrawn]) -> None:
        data = {
            r.name: {"version": r.version, "reason": r.reason, "acknowledged": r.acknowledged}
            for r in records.values()
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def acknowledge(self, name: str) -> None:
        records = self.all()
        if name in records:
            records[name].acknowledged = True
            self.save(records)


def from_catalog(skill: Skill) -> bool:
    return skill.source == "user" and str(read_meta(skill.path).get("source", "")).startswith("catalog ")


def _newer(a: str, b: str) -> bool:
    def parts(v: str) -> list[int] | None:
        try:
            return [int(x) for x in v.split(".")]
        except ValueError:
            return None

    pa, pb = parts(a), parts(b)
    return pa > pb if pa is not None and pb is not None else a != b and a > b


def check_catalog(
    catalog: Catalog,
    skills: list[Skill],
    store: RevocationStore,
    disable: Callable[[str], None],
    enable: Callable[[str], None],
) -> tuple[list[Withdrawn], list[Update]]:
    """Switch off newly withdrawn versions, switch a skill back on once the user has replaced the withdrawn
    version, and list what is withdrawn and what has an update."""
    known = store.all()
    now: dict[str, Withdrawn] = {}
    updates: list[Update] = []
    listed = {e.name: e for e in catalog.skills}
    for skill in skills:
        entry = listed.get(skill.manifest.name)
        if entry is None or not from_catalog(skill):
            continue
        name, have = skill.manifest.name, skill.manifest.version
        gone = entry.revocation(have)
        if gone:
            before = known.get(name)
            if before is not None and before.version == have:
                now[name] = Withdrawn(name, have, gone.reason, before.acknowledged)  # already acted on
            else:
                disable(name)
                now[name] = Withdrawn(name, have, gone.reason)
        else:
            if name in known and known[name].version != have:
                enable(name)  # we switched it off for an older version; this one is fine
            if not _newer(entry.version, have) or entry.revocation():
                continue
            approved = read_meta(skill.path).get("approved")
            approved = set(approved) if isinstance(approved, list) else set()
            updates.append(Update(name, entry.version, have, sorted(set(entry.permissions) - approved)))
    store.save(now)
    return sorted(now.values(), key=lambda w: w.name), sorted(updates, key=lambda u: u.name)
