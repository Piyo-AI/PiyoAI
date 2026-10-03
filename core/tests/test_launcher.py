from __future__ import annotations

import json
import os
import subprocess
import sys


def test_core_exits_when_the_app_closes_its_stdin(tmp_path):
    env = {
        **os.environ,
        "PIYO_EXIT_ON_STDIN_EOF": "1",
        "PIYO_DATA_DIR": str(tmp_path),
        "PIYO_PORT": "",
        "PIYO_TOKEN": "",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "piyo.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=env,
        text=True,
    )
    try:
        hello = json.loads(proc.stdout.readline())
        assert hello["port"] > 0 and hello["token"]
        proc.stdin.close()
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
