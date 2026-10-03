"""`SKILL.md` parsing and validation (format: PLAN.md §5)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)\Z", re.DOTALL)
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SCRIPT_SUFFIXES = {".py", ".js", ".ts"}


class SkillError(ValueError):
    """A skill that can't be loaded; the message is meant for the skill author."""


class ModelNeeds(BaseModel):
    tool_calling: bool = False
    vision: bool = False
    min_context: int | None = None


class Requires(BaseModel):
    tools: list[str] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    model: ModelNeeds = Field(default_factory=ModelNeeds)


class SkillManifest(BaseModel):
    # Unknown keys are ignored so skills written for other Agent Skills tools still load.
    name: str = Field(max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    version: str = "0.1.0"
    author: str | None = None
    license: str | None = None
    risk: str | None = None  # informational; the gate still enforces
    requires: Requires = Field(default_factory=Requires)
    runtime: dict | None = None

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not _NAME.fullmatch(v):
            raise ValueError("must be lowercase letters, digits and single dashes")
        return v

    @field_validator("version", mode="before")
    @classmethod
    def _version_str(cls, v: object) -> str:
        return str(v)  # YAML reads `1.0` as a float


@dataclass
class Skill:
    manifest: SkillManifest
    path: Path
    body: str
    source: str  # "builtin" | "user"
    scripts: list[str] = field(default_factory=list)

    @property
    def has_setup(self) -> bool:
        return (self.path / "SETUP.md").is_file()


def parse_skill_md(text: str) -> tuple[SkillManifest, str]:
    match = _FRONTMATTER.match(text.lstrip("﻿"))
    if not match:
        raise SkillError("SKILL.md must start with a '---' YAML frontmatter block")
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        raise SkillError(f"frontmatter is not valid YAML: {e}") from None
    if not isinstance(data, dict):
        raise SkillError("frontmatter must be a YAML mapping")
    try:
        manifest = SkillManifest.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(map(str, err['loc'])) or 'frontmatter'}: {err['msg']}" for err in e.errors()
        )
        raise SkillError(problems) from None
    return manifest, match.group(2)


def load_skill_dir(path: Path, source: str) -> Skill:
    skill_md = path / "SKILL.md"
    if not skill_md.is_file():
        raise SkillError("missing SKILL.md")
    manifest, body = parse_skill_md(skill_md.read_text(encoding="utf-8"))
    if manifest.name != path.name:
        raise SkillError(f"name {manifest.name!r} must match its folder name {path.name!r}")
    scripts_dir = path / "scripts"
    scripts = (
        sorted(p.name for p in scripts_dir.iterdir() if p.suffix in _SCRIPT_SUFFIXES)
        if scripts_dir.is_dir()
        else []
    )
    return Skill(manifest=manifest, path=path, body=body, source=source, scripts=scripts)
