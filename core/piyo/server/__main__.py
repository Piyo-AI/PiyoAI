"""Entry point used by the desktop app (as a sidecar) and for development.

Prints one JSON line {"port": ..., "token": ...} to stdout so the app can connect.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys

import uvicorn
from dotenv import find_dotenv, load_dotenv

from piyo.server.app import create_app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    port = int(os.environ.get("PIYO_PORT") or _free_port())
    token = os.environ.get("PIYO_TOKEN") or secrets.token_urlsafe(32)
    print(json.dumps({"port": port, "token": token}), flush=True)
    sys.stdout.flush()
    uvicorn.run(create_app(token), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
