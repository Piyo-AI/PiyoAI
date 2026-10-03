"""API keys and tokens live in the OS keychain, never in config files or prompts.

Windows Credential Manager, macOS Keychain and Secret Service (Linux) via `keyring`.
For development, environment variables (or a repo-root `.env`) are used as a fallback.
"""

from __future__ import annotations

import os

import keyring
from keyring.errors import KeyringError

SERVICE = "PiyoAI"


def get_secret(name: str, env_fallback: str | None = None) -> str | None:
    try:
        value = keyring.get_password(SERVICE, name)
    except KeyringError:
        value = None
    if value:
        return value
    if env_fallback:
        return os.environ.get(env_fallback) or None
    return None


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def delete_secret(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except KeyringError:
        pass
