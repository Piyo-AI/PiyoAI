"""The GitHub access token for installing a skill from a private repository.

Kept in the OS keychain like every other secret. It is only ever sent to api.github.com and
codeload.github.com (`catalog._TOKEN_HOSTS`) and only by `git_source`; the public catalog client never carries
it.
"""

from __future__ import annotations

import re

from piyo.config.secrets import delete_secret, get_secret, set_secret

SECRET_NAME = "git.github"
# Classic and fine-grained tokens. No whitespace, so nothing can be smuggled into a header.
_VALID = re.compile(r"[A-Za-z0-9_\-.]{20,255}")


def get_token() -> str | None:
    return get_secret(SECRET_NAME)


def set_token(token: str) -> None:
    token = token.strip()
    if not _VALID.fullmatch(token):
        raise ValueError("That does not look like a GitHub token. Copy it again, without spaces.")
    set_secret(SECRET_NAME, token)


def delete_token() -> None:
    delete_secret(SECRET_NAME)
