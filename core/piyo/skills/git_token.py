"""Access tokens for installing a skill from a private repository, one per Git host.

Kept in the OS keychain like every other secret (`git.github`, `git.gitlab`, `git.codeberg`). A token is
only ever sent to the names of its own host (`GitHost.api_hosts`) and only by `GitClient`; the public
catalog client never carries one.
"""

from __future__ import annotations

import re

from piyo.config.secrets import delete_secret, get_secret, set_secret
from piyo.skills.git_hosts import HOSTS

# Classic and fine-grained tokens. No whitespace, so nothing can be smuggled into a header.
_VALID = re.compile(r"[A-Za-z0-9_\-.]{20,255}")


def _name(host: str) -> str:
    if host not in HOSTS:
        raise KeyError(host)
    return f"git.{host}"


def get_token(host: str) -> str | None:
    return get_secret(_name(host))


def set_token(host: str, token: str) -> None:
    token = token.strip()
    if not _VALID.fullmatch(token):
        raise ValueError(f"That does not look like a {HOSTS[host].name} token. Copy it again without spaces.")
    set_secret(_name(host), token)


def delete_token(host: str) -> None:
    delete_secret(_name(host))


def saved() -> dict[str, bool]:
    return {host: bool(get_token(host)) for host in HOSTS}
