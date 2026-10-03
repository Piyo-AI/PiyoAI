"""Starts a skill's Python script inside its own environment (run by `ScriptRunner`, never imported).

Unless the skill declared network access, sockets are blocked first. This guards against a script reaching
the network by accident; it is not a sandbox against hostile code (see `runner.py`).
"""

import os
import runpy
import socket
import sys


def _blocked(*args, **kwargs):
    raise PermissionError("This skill did not declare network access (runtime.python.network).")


if os.environ.get("PIYO_ALLOW_NET") != "1":
    socket.socket.connect = _blocked
    socket.socket.connect_ex = _blocked
    socket.getaddrinfo = _blocked
    socket.create_connection = _blocked

script = sys.argv[1]
sys.path.insert(0, os.path.dirname(script))
sys.argv = [script]
runpy.run_path(script, run_name="__main__")
