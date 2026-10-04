"""Install a skill from a Git address, pinned to one commit (PLAN.md section 5).

Accepted hosts: GitHub, GitLab.com and Codeberg (`git_hosts.py`: how each host's addresses look, e.g.
`https://github.com/<owner>/<repo>/tree/<ref>/<folder>`; a ref may contain slashes, like `feature/x`). The
branch or tag is resolved to a commit hash first and that exact commit is downloaded as an archive, so nothing
moves under the user between the review and the install. No `git` program runs, and only the host's own fixed
names are contacted. Everything installed this way is unverified.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from piyo.skills.catalog import MAX_DOWNLOAD_BYTES, CatalogNotFound
from piyo.skills.git_hosts import GitAddress, GitClient, parse_address
from piyo.skills.install import InstallError, Preview, SkillInstaller

MAX_CHOICES = 30


@dataclass
class GitChoice:
    """The address points at a repo with several skills; the user picks one."""

    commit: str
    folders: list[str]


def skill_folders(data: bytes) -> list[str]:
    """Folders (relative to the repository root) that hold a SKILL.md, shallowest first."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        raise InstallError("The host sent something that is not a zip.") from None
    found = []
    for name in names:
        parts = PurePosixPath(name).parts
        if len(parts) >= 2 and parts[-1] == "SKILL.md" and len(parts) <= 6:
            found.append("/".join(parts[1:-1]))
    return sorted(set(found), key=lambda f: (f.count("/"), f))


async def _resolve_address(client: GitClient, address: GitAddress) -> tuple[str, str]:
    """The commit and the folder: tries the shortest ref first, then longer ones (branches with slashes)."""
    error: CatalogNotFound | None = None
    for ref, path in address.candidates():
        try:
            return await client.resolve(address.repo, ref), path
        except CatalogNotFound as e:
            error = e
    raise error or InstallError("That address could not be resolved.")


async def stage_git(
    client_for, installer: SkillInstaller, url: str, folder: str | None = None, commit: str | None = None
) -> Preview | GitChoice:
    """Resolve, download and stage; returns the review, or the list of skills to choose from.

    `client_for(host)` makes the `GitClient` for that host (the server adds the user's token there)."""
    address = parse_address(url)
    client: GitClient = client_for(address.host)
    if commit is not None and not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise InstallError("That is not a commit.")
    path = address.path
    if commit is None:
        commit, path = await _resolve_address(client, address)
    data = await client.archive(address.repo, commit)
    wanted = path if folder is None else folder.strip("/")
    if folder is None and not path:
        folders = skill_folders(data)
        if not folders:
            raise InstallError("There is no SKILL.md in that repository.")
        if folders != [""] and "" not in folders:
            if len(folders) > 1:
                return GitChoice(commit, folders[:MAX_CHOICES])
            wanted = folders[0]
    source = f"git {address.host.domain}/{address.repo} @ {commit[:7]}" + (f" ({wanted})" if wanted else "")
    return installer.stage_zip(data, subpath=wanted, source=source, max_zip_bytes=MAX_DOWNLOAD_BYTES)
