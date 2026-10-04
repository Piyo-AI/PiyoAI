"""Install a skill from a GitHub address, pinned to one commit (PLAN.md section 5).

Accepted: `https://github.com/<owner>/<repo>`, `.../tree/<ref>` and `.../tree/<ref>/<folder>` (a ref may
contain slashes, like `feature/x`). The branch or tag is resolved to a commit hash first and that exact
commit is downloaded as an archive, so nothing moves under the user between the review and the install.
No `git` program runs, and only api.github.com and codeload.github.com are contacted (the same fixed
hosts as the catalog). Everything installed this way is unverified.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from piyo.skills.catalog import MAX_DOWNLOAD_BYTES, CatalogClient, CatalogNotFound
from piyo.skills.install import InstallError, Preview, SkillInstaller

_NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}")
MAX_CHOICES = 30


@dataclass
class GitAddress:
    repo: str  # owner/name
    ref: str | None
    path: str  # folder inside the repo, "" for the root
    # Everything after `/tree/`. A branch name may contain slashes, so where the ref ends and the folder
    # starts is only known once GitHub has been asked: `candidates()` lists the splits to try.
    tree: tuple[str, ...] = ()

    def candidates(self) -> list[tuple[str | None, str]]:
        if not self.tree:
            return [(None, "")]
        return [("/".join(self.tree[:i]), "/".join(self.tree[i:])) for i in range(1, len(self.tree) + 1)]


@dataclass
class GitChoice:
    """The address points at a repo with several skills; the user picks one."""

    commit: str
    folders: list[str]


def parse_address(url: str) -> GitAddress:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in ("github.com", "www.github.com") or parsed.port:
        raise InstallError("Only https://github.com/... addresses are supported for now.")
    if parsed.username or parsed.password or parsed.query:
        raise InstallError("Use the plain address of the repository, without a login or parameters.")
    parts = [unquote(p) for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise InstallError(
            "That address needs an owner and a repository, like https://github.com/owner/repo."
        )
    owner, name = parts[0], parts[1].removesuffix(".git")
    ref, folder, tree = None, "", ()
    if len(parts) > 2:
        if parts[2] != "tree" or len(parts) < 4:
            raise InstallError(
                "Use the repository address, or one that points to a folder (.../tree/<branch>/<folder>)."
            )
        ref, folder, tree = parts[3], "/".join(parts[4:]), tuple(parts[3:])
    if not _NAME.fullmatch(owner) or not _NAME.fullmatch(name):
        raise InstallError("That does not look like a GitHub repository address.")
    if any(".." in s for s in tree) or ".." in PurePosixPath(folder).parts:
        raise InstallError("That folder path is not allowed.")
    return GitAddress(f"{owner}/{name}", ref, folder, tree)


def skill_folders(data: bytes) -> list[str]:
    """Folders (relative to the repository root) that hold a SKILL.md, shallowest first."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        raise InstallError("GitHub sent something that is not a zip.") from None
    found = []
    for name in names:
        parts = PurePosixPath(name).parts
        if len(parts) >= 2 and parts[-1] == "SKILL.md" and len(parts) <= 6:
            found.append("/".join(parts[1:-1]))
    return sorted(set(found), key=lambda f: (f.count("/"), f))


async def _resolve_address(client: CatalogClient, address: GitAddress) -> tuple[str, str]:
    """The commit and the folder: tries the shortest ref first, then longer ones (branches with slashes)."""
    error: CatalogNotFound | None = None
    for ref, path in address.candidates():
        try:
            return await client._resolve(ref or "HEAD"), path
        except CatalogNotFound as e:
            error = e
    raise error or InstallError("That address could not be resolved.")


async def stage_git(
    client_for, installer: SkillInstaller, url: str, folder: str | None = None, commit: str | None = None
) -> Preview | GitChoice:
    """Resolve, download and stage; returns the review, or the list of skills to choose from."""
    address = parse_address(url)
    client: CatalogClient = client_for(address.repo)
    if commit is not None and not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise InstallError("That is not a commit.")
    path = address.path
    if commit is None:
        commit, path = await _resolve_address(client, address)
    data = await client._get(f"https://codeload.github.com/{address.repo}/zip/{commit}", MAX_DOWNLOAD_BYTES)
    wanted = path if folder is None else folder.strip("/")
    if folder is None and not path:
        folders = skill_folders(data)
        if not folders:
            raise InstallError("There is no SKILL.md in that repository.")
        if folders != [""] and "" not in folders:
            if len(folders) > 1:
                return GitChoice(commit, folders[:MAX_CHOICES])
            wanted = folders[0]
    source = f"git github.com/{address.repo} @ {commit[:7]}" + (f" ({wanted})" if wanted else "")
    return installer.stage_zip(data, subpath=wanted, source=source, max_zip_bytes=MAX_DOWNLOAD_BYTES)
