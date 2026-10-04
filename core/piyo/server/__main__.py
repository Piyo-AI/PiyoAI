"""Entry point used by the desktop app (as a sidecar) and for development.

Prints one JSON line {"port": ..., "token": ...} to stdout so the app can connect.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import threading

import uvicorn
from dotenv import find_dotenv, load_dotenv

from piyo.runtime import frozen, use_shared_browser_cache
from piyo.server.app import create_app
from piyo.telemetry import Telemetry, install_crash_hooks


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _exit_when_stdin_closes() -> None:
    """The desktop app holds our stdin open; when it goes away (even if killed), so do we."""

    def watch() -> None:
        try:
            while sys.stdin.read(1024):
                pass
        except (OSError, ValueError):
            pass
        os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def main() -> None:
    use_shared_browser_cache()
    if frozen():
        # The shipped core always gets a fresh random port and token: a stray .env in the working directory or
        # a PIYO_TOKEN in the environment must not be able to fix them.
        port, token = _free_port(), secrets.token_urlsafe(32)
    else:
        load_dotenv(find_dotenv(usecwd=True))
        port = int(os.environ.get("PIYO_PORT") or _free_port())
        token = os.environ.get("PIYO_TOKEN") or secrets.token_urlsafe(32)
    install_crash_hooks(Telemetry())
    print(json.dumps({"port": port, "token": token}), flush=True)
    sys.stdout.flush()
    if os.environ.get("PIYO_EXIT_ON_STDIN_EOF"):
        _exit_when_stdin_closes()
    uvicorn.run(create_app(token), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
