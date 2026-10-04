"""The public skill catalog: the `Piyo-AI/piyo-skills` repo (index format: PLAN.md section 5).

One refresh resolves the branch to a single commit, then reads the index and downloads the package from that
same commit, so the two cannot disagree. Only three fixed GitHub hosts over https are ever contacted, and the
package is checked against the index's hash before the user is shown it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from piyo.skills.install import InstallError, Preview, SkillInstaller

REPO = "Piyo-AI/piyo-skills"
BRANCH = "main"
MAX_INDEX_BYTES = 1024 * 1024
MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{40}")
_GITHUB_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
SCHEMAS = (1, 2)  # index versions this app reads; 2 added category, badge, revoked and external sources
_HOSTS = {"api.github.com", "raw.githubusercontent.com", "codeload.github.com"}


class CatalogError(RuntimeError):
    """The catalog could not be reached or read; the message is shown to the user."""


class CatalogNotFound(CatalogError):
    """GitHub says the repository, branch, tag or commit does not exist."""


class Revocation(BaseModel):
    version: str  # "*" revokes every version
    reason: str


class GitSource(BaseModel):
    """Where an external skill lives: a GitHub repo pinned to one commit (never a branch or tag)."""

    type: str = "git"
    url: str
    commit: str
    subpath: str = ""

    @field_validator("type")
    @classmethod
    def _git_only(cls, v: str) -> str:
        if v != "git":
            raise ValueError("unknown source type")
        return v

    @field_validator("url")
    @classmethod
    def _github(cls, v: str) -> str:
        if not _GITHUB_URL.fullmatch(v):
            raise ValueError("not a GitHub repository")
        return v

    @field_validator("commit")
    @classmethod
    def _pinned(cls, v: str) -> str:
        if not _SHA.fullmatch(v):
            raise ValueError("not a full commit hash")
        return v

    @property
    def repo(self) -> str:
        return _GITHUB_URL.fullmatch(self.url).group(1)  # type: ignore[union-attr]


class CatalogEntry(BaseModel):
    name: str
    version: str
    description: str
    author: str | None = None
    license: str | None = None
    path: str
    sha256: str
    files: int = 0
    size: int = 0
    permissions: list[str] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    model: dict = Field(default_factory=dict)
    category: str = "other"  # free text here: a newer catalog may add categories this app has not heard of
    badge: str = "community"  # official | verified | community; the catalog team sets it, not the skill
    revoked: list[Revocation] = Field(default_factory=list)
    source: GitSource | None = None  # None: the package is in the catalog repo itself, at `path`

    def revocation(self, version: str | None = None) -> Revocation | None:
        """Why `version` (default: the listed one) was withdrawn, or None if it was not."""
        version = version or self.version
        return next((r for r in self.revoked if r.version in (version, "*")), None)


@dataclass
class Catalog:
    commit: str
    skills: list[CatalogEntry] = field(default_factory=list)
    skipped: int = 0  # entries this app version could not read


class CatalogClient:
    def __init__(
        self,
        repo: str = REPO,
        branch: str = BRANCH,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.repo, self.branch, self._transport = repo, branch, transport

    async def _get(self, url: str, limit: int, accept: str | None = None) -> bytes:
        host = httpx.URL(url).host
        if not url.startswith("https://") or host not in _HOSTS:
            raise CatalogError("Refusing to contact an unexpected address.")
        headers = {"User-Agent": "PiyoAI", **({"Accept": accept} if accept else {})}
        try:
            async with (
                httpx.AsyncClient(
                    transport=self._transport, follow_redirects=False, timeout=20.0, headers=headers
                ) as client,
                client.stream("GET", url) as res,
            ):
                if res.status_code == 404:
                    raise CatalogNotFound("The skill catalog was not found. Is the repository public?")
                if res.status_code == 422 and "/commits/" in url:  # GitHub's answer for an unknown ref
                    raise CatalogNotFound("That branch, tag or commit was not found.")
                if res.status_code in (403, 429):
                    raise CatalogError("GitHub is limiting requests right now. Try again in a few minutes.")
                if res.status_code != 200:
                    raise CatalogError(f"The skill catalog answered with an error ({res.status_code}).")
                data = bytearray()
                async for chunk in res.aiter_bytes():
                    data += chunk
                    if len(data) > limit:
                        raise CatalogError("The catalog download is larger than expected, so it was stopped.")
                return bytes(data)
        except httpx.TimeoutException:
            raise CatalogError("The skill catalog did not answer in time.") from None
        except httpx.HTTPError:
            raise CatalogError("Could not reach the skill catalog. Check your internet connection.") from None

    async def _resolve(self, ref: str | None = None) -> str:
        data = await self._get(
            f"https://api.github.com/repos/{self.repo}/commits/{ref or self.branch}",
            64 * 1024,
            accept="application/vnd.github.sha",
        )
        sha = data.decode("ascii", errors="replace").strip()
        if not _SHA.fullmatch(sha):
            raise CatalogError("The catalog sent an unexpected answer.")
        return sha

    async def fetch(self, commit: str | None = None) -> Catalog:
        """The index at `commit`, or at the branch head when none is given."""
        if commit is not None and not _SHA.fullmatch(commit):
            raise CatalogError("That is not a commit.")
        commit = commit or await self._resolve()
        raw = await self._get(
            f"https://raw.githubusercontent.com/{self.repo}/{commit}/index.json", MAX_INDEX_BYTES
        )
        try:
            doc = json.loads(raw)
        except ValueError:
            raise CatalogError("The catalog index is not valid.") from None
        if (
            not isinstance(doc, dict)
            or doc.get("schema") not in SCHEMAS
            or not isinstance(doc.get("skills"), list)
        ):
            raise CatalogError("This catalog needs a newer version of Piyo.")
        catalog = Catalog(commit=commit)
        for item in doc["skills"]:
            try:
                catalog.skills.append(CatalogEntry.model_validate(item))
            except ValidationError:
                catalog.skipped += 1  # one bad entry must not hide the others
        return catalog

    async def stage(self, installer: SkillInstaller, name: str, commit: str | None = None) -> Preview:
        """Download one skill at a pinned commit and stage it for review (nothing is installed)."""
        catalog = await self.fetch(commit)
        entry = next((e for e in catalog.skills if e.name == name), None)
        if entry is None:
            raise InstallError(f"The catalog has no skill called {name!r}.")
        gone = entry.revocation()
        if gone:
            raise InstallError(f"The catalog withdrew {entry.name} {entry.version}: {gone.reason}")
        src = entry.source
        repo, ref, subpath = (
            (src.repo, src.commit, src.subpath) if src else (self.repo, catalog.commit, entry.path)
        )
        data = await self._get(f"https://codeload.github.com/{repo}/zip/{ref}", MAX_DOWNLOAD_BYTES)
        preview = installer.stage_zip(
            data,
            subpath=subpath,
            expected_sha256=entry.sha256,
            source=f"catalog {self.repo} @ {catalog.commit[:7]}"
            + (f", from {repo} @ {ref[:7]}" if src else ""),
            max_zip_bytes=MAX_DOWNLOAD_BYTES,
        )
        if preview.name != entry.name or preview.version != entry.version:
            installer.cancel(preview.token)
            raise InstallError("The download does not match what the catalog lists, so it was not installed.")
        return preview
