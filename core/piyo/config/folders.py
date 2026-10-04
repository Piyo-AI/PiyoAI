"""Folders the user has approved for file tools. Default is none."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from piyo.config import data_dir
from piyo.config.protected import broad_reason


@dataclass(frozen=True)
class FolderGrant:
    path: Path
    # Creating folders, moving files and writing new files here need no per-call approval.
    # Deleting and overwriting always ask, whatever this says.
    auto_changes: bool = False


class ApprovedFolders:
    """Persisted grants, re-read on each call so Settings changes apply to a running agent."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or data_dir() / "approved_folders.json"

    def list(self) -> list[FolderGrant]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, list):
            return []
        grants = []
        for item in raw:
            if isinstance(item, str):  # first version of the file: bare paths, always ask
                grants.append(FolderGrant(Path(item)))
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                grants.append(FolderGrant(Path(item["path"]), item.get("auto_changes") is True))
        return grants

    def set(self, grants: list[FolderGrant]) -> list[FolderGrant]:
        """Replace the list. Every path must be an existing directory given in full."""
        resolved: dict[Path, FolderGrant] = {}
        for grant in grants:
            p = grant.path.expanduser()
            if not p.is_absolute():
                raise ValueError(f"Use a full path, not {str(grant.path)!r}.")
            p = p.resolve()
            if not p.is_dir():
                raise ValueError(f"Not a folder: {grant.path}")
            if reason := broad_reason(p):
                raise ValueError(f"{p}: {reason}")
            resolved[p] = FolderGrant(p, grant.auto_changes)
        out = list(resolved.values())
        self.path.write_text(
            json.dumps([{"path": str(g.path), "auto_changes": g.auto_changes} for g in out]),
            encoding="utf-8",
        )
        return out
