"""Installing, updating and removing user skills from a `.piyoskill` zip (PLAN.md §5).

Flow: `stage_zip` unpacks into a private staging folder and returns a `Preview` (what the skill is and every
permission it asks for). Nothing touches the skills folder until `commit` is called with the permissions the
user approved. A fresh install needs all of them; an update needs the ones the installed version never had.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import stat
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from piyo.skills.manifest import Skill, SkillError, load_skill_dir, parse_skill_md

META_FILE = ".piyo-install.json"
MAX_ZIP_BYTES = 10 * 1024 * 1024
MAX_FILES = 200
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024


class InstallError(ValueError):
    """Why a skill can't be installed; the message is shown to the user."""


def permissions_of(skill: Skill) -> list[str]:
    """Every capability the skill asks for, as stable labels the review screen shows."""
    req = skill.manifest.requires
    labels = [f"tool:{t}" for t in req.tools]
    labels += [f"integration:{i}" for i in req.integrations]
    labels += [f"secret:{s}" for s in req.secrets]
    labels += [f"script:{s}" for s in skill.scripts]
    labels += [f"runtime:{r}" for r in sorted(skill.manifest.runtime or {})]
    return sorted(set(labels))


def package_hash(path: Path) -> str:
    """Content hash of a skill folder, the same on every OS.

    It is sha256 over the sorted lines "<posix path> <file sha256>". The catalog index records it
    (PLAN.md §5) and the app recomputes it on what it downloaded. Install metadata is left out, since it
    is written after the hash is checked.
    """
    lines = []
    for file in sorted(p for p in path.rglob("*") if p.is_file() and p.name != META_FILE):
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        lines.append(f"{file.relative_to(path).as_posix()} {digest}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


@dataclass
class Preview:
    token: str
    name: str
    version: str
    description: str
    author: str | None
    source: str  # shown as-is: "zip <sha256 prefix>"
    permissions: list[str]
    files: list[str]
    installed_version: str | None = None  # set when this is an update
    added: list[str] = field(default_factory=list)  # what the user must approve
    verified: bool = False  # only the signed catalog can be verified; zips never are


def read_meta(path: Path) -> dict:
    try:
        raw = json.loads((path / META_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _approved(path: Path) -> list[str]:
    raw = read_meta(path).get("approved")
    return [p for p in raw if isinstance(p, str)] if isinstance(raw, list) else []


def _installed_version(path: Path) -> str | None:
    try:
        return load_skill_dir(path, "user").manifest.version
    except (SkillError, OSError, UnicodeDecodeError):
        return None


BACKUP_FILE = ".piyo-backup.json"


def backup_skill(path: Path, backups_root: Path, reason: str, move: bool = False) -> Path:
    """Keep a copy of a skill folder (or move it away) before it changes. Newest sorts last by name."""
    version = _installed_version(path) or "unknown"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    dest = backups_root / path.name / f"{stamp}-{uuid.uuid4().hex[:4]}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(path), str(dest))
    else:
        shutil.copytree(path, dest)
    info = {"version": version, "at": datetime.now(UTC).isoformat(timespec="seconds"), "reason": reason}
    (dest / BACKUP_FILE).write_text(json.dumps(info), encoding="utf-8")
    return dest


class SkillInstaller:
    def __init__(self, user_dir: Path, work_dir: Path, builtin_names: set[str] | None = None) -> None:
        self.user_dir = user_dir
        self.staging = work_dir / "staging"
        self.backups = work_dir / "backups"
        self.builtin_names = builtin_names or set()

    # -- staging ---------------------------------------------------------------------------

    def stage_zip(
        self,
        data: bytes,
        *,
        subpath: str | None = None,
        expected_sha256: str | None = None,
        source: str | None = None,
        max_zip_bytes: int = MAX_ZIP_BYTES,
    ) -> Preview:
        """Unpack a skill zip and describe it. Nothing is installed.

        `subpath` picks one skill folder out of a repo archive (`<repo>-<ref>/<subpath>/...`); the catalog
        uses it. `expected_sha256` is the package hash the catalog promised; a different one refuses the
        install.
        """
        if len(data) > max_zip_bytes:
            raise InstallError(f"That file is too large to be a skill (limit {max_zip_bytes // 2**20} MB).")
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise InstallError("That file is not a valid zip.") from None
        with zf:
            if subpath is not None:
                entries, root = self._subpath_entries(zf, subpath)
            else:
                entries = self._checked_entries(zf)
                root = self._skill_root(entries)
            token = uuid.uuid4().hex
            folder = self.staging / token  # holds <name>/ and preview.json once staged
            try:
                self._extract(zf, entries, root, folder / "_pending")
                digest = hashlib.sha256(data).hexdigest()
                preview = self._preview(
                    folder, token, digest, source or f"zip {digest[:12]}", expected_sha256
                )
            except BaseException:
                shutil.rmtree(folder, ignore_errors=True)
                raise
        return preview

    @classmethod
    def _checked_entries(cls, zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        return cls._checked_entries_of([i for i in zf.infolist() if not i.is_dir()])

    @staticmethod
    def _checked_entries_of(files: list[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
        if not files:
            raise InstallError("The zip is empty.")
        if len(files) > MAX_FILES:
            raise InstallError(f"Too many files in the zip (limit {MAX_FILES}).")
        total = 0
        for info in files:
            name = info.filename
            parts = PurePosixPath(name.replace("\\", "/")).parts
            if name.startswith(("/", "\\")) or ".." in parts or (parts and ":" in parts[0]):
                raise InstallError(f"Unsafe path in the zip: {name!r}.")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise InstallError(f"Links are not allowed in a skill ({name!r}).")
            if info.flag_bits & 0x1:
                raise InstallError("Encrypted zips are not supported.")
            if info.file_size > MAX_FILE_BYTES:
                raise InstallError(f"{name!r} is too large (limit 2 MB per file).")
            total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise InstallError("The skill is too large once unpacked (limit 10 MB).")
        return files

    def _subpath_entries(self, zf: zipfile.ZipFile, subpath: str) -> tuple[list[zipfile.ZipInfo], str]:
        want = PurePosixPath(subpath).parts
        if ".." in want or subpath.startswith(("/", "\\")):
            raise InstallError(f"Unsafe skill path: {subpath!r}.")
        picked, tops = [], set()
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = PurePosixPath(info.filename.replace("\\", "/")).parts
            if len(parts) > len(want) + 1 and parts[1 : 1 + len(want)] == want:
                picked.append(info)
                tops.add(parts[0])
        if len(tops) != 1:
            raise InstallError(f"{subpath!r} was not found in the download.")
        root = "/".join([next(iter(tops)), *want])
        entries = self._checked_entries_of(picked)
        if not any(i.filename == f"{root}/SKILL.md" for i in entries):
            raise InstallError(f"{subpath!r} has no SKILL.md.")
        return entries, root

    @staticmethod
    def _skill_root(entries: list[zipfile.ZipInfo]) -> str:
        """The folder inside the zip that holds SKILL.md ('' for the top level)."""
        roots = []
        for info in entries:
            parts = PurePosixPath(info.filename.replace("\\", "/")).parts
            if parts[-1] == "SKILL.md" and len(parts) <= 2:
                roots.append("/".join(parts[:-1]))
        if len(roots) != 1:
            raise InstallError("The zip must contain exactly one SKILL.md, at the top or inside one folder.")
        return roots[0]

    @staticmethod
    def _extract(zf: zipfile.ZipFile, entries: list[zipfile.ZipInfo], root: str, dest: Path) -> None:
        dest.mkdir(parents=True)
        prefix = f"{root}/" if root else ""
        written = 0
        for info in entries:
            rel = info.filename.replace("\\", "/")
            if prefix and not rel.startswith(prefix):
                raise InstallError(f"Unexpected file outside the skill folder: {info.filename!r}.")
            rel = rel[len(prefix) :]
            if rel.split("/")[-1] == META_FILE:
                continue  # never trust install metadata that came inside a package
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # Count real bytes: the declared size in a zip header can lie.
            with zf.open(info) as src, target.open("wb") as out:
                while chunk := src.read(64 * 1024):
                    written += len(chunk)
                    if written > MAX_TOTAL_BYTES:
                        raise InstallError("The skill is too large once unpacked (limit 10 MB).")
                    out.write(chunk)

    def _preview(
        self, folder: Path, token: str, digest: str, source: str, expected_sha256: str | None
    ) -> Preview:
        pending = folder / "_pending"
        try:
            manifest, _ = parse_skill_md((pending / "SKILL.md").read_text(encoding="utf-8"))
            final = folder / manifest.name  # load_skill_dir wants the folder named after the skill
            pending.rename(final)
            skill = load_skill_dir(final, "user")
        except (SkillError, UnicodeDecodeError) as e:
            raise InstallError(f"This is not a valid skill: {e}") from None
        name = skill.manifest.name
        if name in self.builtin_names:
            raise InstallError(f"A built-in skill is already called {name!r}.")
        if expected_sha256 is not None and package_hash(final) != expected_sha256:
            raise InstallError(
                "The download does not match the catalog's fingerprint, so it was not installed."
            )
        perms = permissions_of(skill)
        installed = self.user_dir / name
        old_version, added = None, perms
        if installed.is_dir():
            old_version = _installed_version(installed)
            previous = _approved(installed)
            added = [p for p in perms if p not in previous]
        staged_info = {"name": name, "digest": digest, "source": source}
        (folder / "preview.json").write_text(json.dumps(staged_info), encoding="utf-8")
        files = sorted(p.relative_to(final).as_posix() for p in final.rglob("*") if p.is_file())
        return Preview(
            token=token,
            name=name,
            version=skill.manifest.version,
            description=skill.manifest.description,
            author=skill.manifest.author,
            source=source,
            permissions=perms,
            files=files,
            installed_version=old_version,
            added=added,
        )

    # -- commit / remove -------------------------------------------------------------------

    def _staged(self, token: str) -> tuple[Path, dict]:
        folder = self.staging / token
        if not token.isalnum() or not (folder / "preview.json").is_file():
            raise InstallError("This install request expired. Choose the file again.")
        return folder, json.loads((folder / "preview.json").read_text(encoding="utf-8"))

    def commit(self, token: str, approved: list[str]) -> Skill:
        folder, info = self._staged(token)
        name = info["name"]
        skill = load_skill_dir(folder / name, "user")
        if name in self.builtin_names:
            raise InstallError(f"A built-in skill is already called {name!r}.")
        target = self.user_dir / name
        previous = _approved(target) if target.is_dir() else []
        missing = [p for p in permissions_of(skill) if p not in previous and p not in approved]
        if missing:
            raise InstallError("These permissions were not approved: " + ", ".join(missing))
        if target.is_dir():
            self._back_up(target)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder / name), str(target))
        shutil.rmtree(folder, ignore_errors=True)
        (target / META_FILE).write_text(
            json.dumps(
                {
                    "source": info["source"],
                    "sha256": info["digest"],
                    "installed_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "approved": permissions_of(skill),  # dropped permissions must be re-approved later
                }
            ),
            encoding="utf-8",
        )
        return load_skill_dir(target, "user")

    def cancel(self, token: str) -> None:
        if token.isalnum():
            shutil.rmtree(self.staging / token, ignore_errors=True)

    def _back_up(self, target: Path) -> None:
        backup_skill(target, self.backups, "update", move=True)

    def uninstall(self, name: str) -> None:
        target = self.user_dir / name
        if name in self.builtin_names or "/" in name or "\\" in name or not target.is_dir():
            raise InstallError("Only skills you installed can be removed.")
        shutil.rmtree(target)
        shutil.rmtree(self.backups / name, ignore_errors=True)
