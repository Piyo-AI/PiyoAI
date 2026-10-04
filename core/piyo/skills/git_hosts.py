"""The Git hosts a skill can be installed from: GitHub, GitLab.com and Codeberg.

Each host knows four things: how its web addresses look, how to turn a branch or tag into one commit hash,
where that commit's archive is, and how to send an access token. `GitClient` does the downloading for any of
them: it contacts only the host's own fixed names over https, never follows a redirect, and sends the user's
token to those names and nowhere else. Self-hosted GitLab, Gitea or Forgejo servers are not supported on
purpose: the list of places the app (and a token) may talk to stays fixed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlparse

import httpx

from piyo.skills.catalog import CatalogError, CatalogNotFound
from piyo.skills.install import InstallError

_NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_SHA = re.compile(r"[0-9a-f]{40}")
MAX_GROUP_DEPTH = 6  # GitLab subgroups: group/sub/sub/project


@dataclass
class GitAddress:
    host: GitHost
    repo: str  # owner/name (GitLab: group/subgroup/project)
    ref: str | None
    path: str  # folder inside the repo, "" for the root
    # Everything after the tree marker. A branch name may contain slashes, so where the ref ends and the
    # folder starts is only known once the host has been asked: `candidates()` lists the splits to try.
    tree: tuple[str, ...] = ()

    def candidates(self) -> list[tuple[str | None, str]]:
        if not self.tree:
            return [(None, "")]
        return [("/".join(self.tree[:i]), "/".join(self.tree[i:])) for i in range(1, len(self.tree) + 1)]


class GitHost:
    key: str  # used in the keychain name and the API
    name: str  # shown to the user
    domain: str  # the one site the addresses are on
    aliases: tuple[str, ...] = ()
    api_hosts: frozenset[str]  # every name this host is contacted on (a token may go to these only)
    example: str

    def _split(self, parts: list[str]) -> tuple[list[str], tuple[str, ...]]:
        """The repository path and the words after the tree marker."""
        raise NotImplementedError

    def resolve_url(self, repo: str, ref: str | None) -> str:
        raise NotImplementedError

    def commit_of(self, body: bytes) -> str:
        raise NotImplementedError

    def archive_url(self, repo: str, commit: str) -> str:
        raise NotImplementedError

    def headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    accept: str | None = None
    not_found = "That repository, branch, tag or commit was not found."

    def parse(self, parsed) -> GitAddress:
        parts = [unquote(p) for p in parsed.path.split("/") if p]
        repo_parts, tree = self._split(parts)
        if len(repo_parts) < 2 or len(repo_parts) > MAX_GROUP_DEPTH + 1:
            raise InstallError(f"That address needs an owner and a repository, like {self.example}.")
        repo_parts[-1] = repo_parts[-1].removesuffix(".git")
        folder = "/".join(tree[1:])
        if not all(_NAME.fullmatch(p) for p in repo_parts):
            raise InstallError(f"That does not look like a {self.name} repository address.")
        if any(".." in s for s in tree) or ".." in PurePosixPath(folder).parts:
            raise InstallError("That folder path is not allowed.")
        return GitAddress(self, "/".join(repo_parts), tree[0] if tree else None, folder, tree)


class GitHub(GitHost):
    key, name, domain = "github", "GitHub", "github.com"
    aliases = ("www.github.com",)
    api_hosts = frozenset({"api.github.com", "codeload.github.com"})
    example = "https://github.com/owner/repo"
    accept = "application/vnd.github.sha"

    def _split(self, parts):
        if len(parts) > 2:
            if parts[2] != "tree" or len(parts) < 4:
                raise InstallError(
                    "Use the repository address, or one that points to a folder (.../tree/<branch>/<folder>)."
                )
            return parts[:2], tuple(parts[3:])
        return parts, ()

    def resolve_url(self, repo, ref):
        return f"https://api.github.com/repos/{repo}/commits/{quote(ref or 'HEAD', safe='/')}"

    def commit_of(self, body):
        return body.decode("ascii", errors="replace").strip()

    def archive_url(self, repo, commit):
        return f"https://codeload.github.com/{repo}/zip/{commit}"


class GitLab(GitHost):
    key, name, domain = "gitlab", "GitLab", "gitlab.com"
    api_hosts = frozenset({"gitlab.com"})
    example = "https://gitlab.com/group/project"

    def _split(self, parts):
        # Groups nest, so the project path ends at the `-` that GitLab puts before its own pages.
        if "-" in parts:
            at = parts.index("-")
            rest = parts[at + 1 :]
            if len(rest) < 2 or rest[0] != "tree":
                raise InstallError(
                    "Use the repository address, or one that points to a folder "
                    "(.../-/tree/<branch>/<folder>)."
                )
            return parts[:at], tuple(rest[1:])
        return parts, ()

    def _project(self, repo: str) -> str:
        return f"https://gitlab.com/api/v4/projects/{quote(repo, safe='')}/repository"

    def resolve_url(self, repo, ref):
        return f"{self._project(repo)}/commits/{quote(ref or 'HEAD', safe='')}"

    def commit_of(self, body):
        data = json.loads(body)
        return str(data.get("id", "")) if isinstance(data, dict) else ""

    def archive_url(self, repo, commit):
        return f"{self._project(repo)}/archive.zip?sha={commit}"

    def headers(self, token):
        return {"PRIVATE-TOKEN": token}


class Codeberg(GitHost):
    key, name, domain = "codeberg", "Codeberg", "codeberg.org"
    api_hosts = frozenset({"codeberg.org"})
    example = "https://codeberg.org/owner/repo"

    def _split(self, parts):
        if len(parts) > 2:
            if len(parts) < 5 or parts[2] != "src" or parts[3] not in ("branch", "tag", "commit"):
                raise InstallError(
                    "Use the repository address, or one that points to a folder "
                    "(.../src/branch/<branch>/<folder>)."
                )
            return parts[:2], tuple(parts[4:])
        return parts, ()

    def _api(self, repo: str) -> str:
        return f"https://codeberg.org/api/v1/repos/{repo}"

    def resolve_url(self, repo, ref):
        return f"{self._api(repo)}/git/commits/{quote(ref or 'HEAD', safe='')}"

    def commit_of(self, body):
        data = json.loads(body)
        return str(data.get("sha", "")) if isinstance(data, dict) else ""

    def archive_url(self, repo, commit):
        return f"{self._api(repo)}/archive/{commit}.zip"

    def headers(self, token):
        return {"Authorization": f"token {token}"}


HOSTS: dict[str, GitHost] = {h.key: h for h in (GitHub(), GitLab(), Codeberg())}
SUPPORTED = ", ".join(f"{h.domain}" for h in HOSTS.values())


def parse_address(url: str) -> GitAddress:
    parsed = urlparse(url.strip())
    host = next(
        (h for h in HOSTS.values() if parsed.hostname in (h.domain, *h.aliases)),
        None,
    )
    if parsed.scheme != "https" or host is None or parsed.port:
        raise InstallError(f"Only https addresses on {SUPPORTED} are supported for now.")
    if parsed.username or parsed.password or parsed.query:
        raise InstallError("Use the plain address of the repository, without a login or parameters.")
    return host.parse(parsed)


class GitClient:
    """Downloads from one host. Only that host's own names are contacted, and only they ever see the token."""

    def __init__(
        self,
        host: GitHost,
        transport: httpx.AsyncBaseTransport | None = None,
        token: str | None = None,
        max_download: int = 30 * 1024 * 1024,
    ) -> None:
        self.host, self._transport, self._token, self.max_download = host, transport, token, max_download

    async def _get(self, url: str, limit: int, accept: str | None = None) -> bytes:
        name = self.host.name
        if not url.startswith("https://") or httpx.URL(url).host not in self.host.api_hosts:
            raise CatalogError("Refusing to contact an unexpected address.")
        headers = {"User-Agent": "PiyoAI", **({"Accept": accept} if accept else {})}
        if self._token:
            headers.update(self.host.headers(self._token))
        try:
            async with (
                httpx.AsyncClient(
                    transport=self._transport, follow_redirects=False, timeout=20.0, headers=headers
                ) as client,
                client.stream("GET", url) as res,
            ):
                if res.status_code == 401 and self._token:
                    raise CatalogError(
                        f"{name} did not accept the access token. Check or replace it in Settings > Skills."
                    )
                if res.status_code in (404, 422):  # 422: GitHub's answer for an unknown ref
                    hint = (
                        "The access token may not be allowed to read it."
                        if self._token
                        else f"If it is private, add a {name} access token in Settings > Skills."
                    )
                    raise CatalogNotFound(f"{self.host.not_found} {hint}")
                if res.status_code in (403, 429):
                    raise CatalogError(f"{name} is limiting requests right now. Try again in a few minutes.")
                if res.status_code != 200:
                    raise CatalogError(f"{name} answered with an error ({res.status_code}).")
                data = bytearray()
                async for chunk in res.aiter_bytes():
                    data += chunk
                    if len(data) > limit:
                        raise CatalogError("The download is larger than expected, so it was stopped.")
                return bytes(data)
        except httpx.TimeoutException:
            raise CatalogError(f"{name} did not answer in time.") from None
        except httpx.HTTPError:
            raise CatalogError(f"Could not reach {name}. Check your internet connection.") from None

    async def resolve(self, repo: str, ref: str | None) -> str:
        """One commit hash for a branch or tag (the default branch when `ref` is None)."""
        body = await self._get(self.host.resolve_url(repo, ref), 256 * 1024, self.host.accept)
        try:
            sha = self.host.commit_of(body).lower()
        except ValueError:
            sha = ""
        if not _SHA.fullmatch(sha):
            raise CatalogError(f"{self.host.name} sent an unexpected answer.")
        return sha

    async def archive(self, repo: str, commit: str) -> bytes:
        return await self._get(self.host.archive_url(repo, commit), self.max_download)
