"""Runs the helper scripts a skill ships (`scripts/*.py`, `*.js`, `*.ts`), PLAN.md section 5.

A script gets one JSON value on stdin and must print one JSON value on stdout. Both runtimes share the limits:
a timeout, a cap on output size, a scrubbed environment (only the secrets the skill declared), and a throw-away
working directory. Anything over a limit is killed and reported; nothing is partially trusted.

What each runtime can and cannot enforce (be honest about it, the user sees this in the install review):
- **Deno** has a real permission sandbox: reads only the skill folder, network only the hosts the skill declared,
  no writes, no subprocesses, no environment access.
- **Python** has no such sandbox. Each skill gets its own virtual environment, a separate process, a scrubbed
  environment and a temporary working directory, and sockets are blocked unless the skill declares network
  access. That socket block is a guard against mistakes, not against hostile code: a script that wants to can
  still read files the user can read. Third-party Python scripts are therefore confirmed on every call.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from piyo.skills.manifest import Skill

MAX_ARGS_BYTES = 100_000
DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 300.0
MAX_OUTPUT_BYTES = 1_000_000
MAX_STDERR_BYTES = 20_000
SETUP_TIMEOUT_S = 300.0  # building a Python environment or caching Deno modules (network)
LAUNCHER = Path(__file__).with_name("_launch.py")


class ScriptError(Exception):
    """A script that could not run or failed; the message goes back to the model and the user."""


@dataclass
class ScriptResult:
    value: object
    seconds: float


def secret_name(skill: str, name: str) -> str:
    """The keychain entry holding one of a skill's declared secrets."""
    return f"skill.{skill}.{name}"


def env_name(name: str) -> str:
    return "PIYO_SECRET_" + "".join(c if c.isalnum() else "_" for c in name.upper())


def _base_env() -> dict[str, str]:
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"}
    for keep in ("PATH", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL"):
        if keep in os.environ:  # what a process needs to start, never the user's keys
            env[keep] = os.environ[keep]
    return env


def _last_lines(raw: bytes, count: int = 3) -> str:
    """The end of a traceback is the useful part."""
    lines = [line.strip() for line in raw.decode("utf-8", "replace").splitlines() if line.strip()]
    return " | ".join(lines[-count:])[-500:]


class ScriptRunner:
    def __init__(
        self,
        work_dir: Path,
        get_secret: Callable[[str], str | None],
        uv: str | None = None,
        deno: str | None = None,
    ) -> None:
        self.work_dir = work_dir
        self._get_secret = get_secret
        self._uv_bin = uv or os.environ.get("PIYO_UV") or shutil.which("uv")
        self._deno_bin = deno or os.environ.get("PIYO_DENO") or shutil.which("deno")

    # -- public ------------------------------------------------------------------------------

    async def run(self, skill: Skill, script: str, args: dict) -> ScriptResult:
        if script not in skill.scripts:
            have = ", ".join(skill.scripts) or "none"
            raise ScriptError(f"The skill {skill.manifest.name!r} has no script {script!r}. It has: {have}.")
        payload = json.dumps(args, ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_ARGS_BYTES:
            raise ScriptError("The arguments are too large for a script.")
        path = skill.path / "scripts" / script
        suffix = path.suffix
        runtime = (skill.manifest.runtime or {}).get("python" if suffix == ".py" else "deno") or {}
        if not isinstance(runtime, dict):
            raise ScriptError("The skill's runtime settings are not valid.")
        timeout = min(max(float(runtime.get("timeout_s", DEFAULT_TIMEOUT_S)), 1.0), MAX_TIMEOUT_S)

        env = _base_env()
        for name in skill.manifest.requires.secrets:
            value = self._get_secret(secret_name(skill.manifest.name, name))
            if value:
                env[env_name(name)] = value
        with tempfile.TemporaryDirectory(prefix="piyo-script-") as cwd:
            if suffix == ".py":
                command, env = await self._python(skill, path, runtime, env)
            else:
                command, env = await self._deno(skill, path, runtime, env)
            started = asyncio.get_running_loop().time()
            out = await self._execute(command, env, cwd, payload, timeout)
            seconds = asyncio.get_running_loop().time() - started
        try:
            return ScriptResult(json.loads(out), seconds)
        except ValueError:
            raise ScriptError("The script did not print one JSON value on stdout.") from None

    # -- python ------------------------------------------------------------------------------

    async def _python(self, skill: Skill, path: Path, runtime: dict, env: dict) -> tuple[list[str], dict]:
        if not self._uv_bin:
            raise ScriptError("Python scripts need uv, which was not found. Install uv (https://docs.astral.sh/uv/).")
        deps = [str(d) for d in runtime.get("dependencies") or []]
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        key = hashlib.sha256(json.dumps([version, sorted(deps)]).encode()).hexdigest()[:12]
        venv = self.work_dir / "skill-envs" / f"{skill.manifest.name}-{key}"
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file():
            shutil.rmtree(venv, ignore_errors=True)
            venv.parent.mkdir(parents=True, exist_ok=True)
            await self._setup([self._uv_bin, "venv", "--python", version, str(venv)], "create the environment")
            if deps:
                await self._setup(
                    [self._uv_bin, "pip", "install", "--python", str(python), *deps], "install the dependencies"
                )
        if runtime.get("network") is True:
            env["PIYO_ALLOW_NET"] = "1"
        return [str(python), "-u", str(LAUNCHER), str(path)], env

    # -- deno --------------------------------------------------------------------------------

    def deno_flags(self, skill: Skill, runtime: dict, cache: Path) -> list[str]:
        """The permission flags: exactly what the manifest declared, nothing else."""
        flags = ["--no-prompt", f"--allow-read={skill.path},{cache}", "--no-lock"]
        hosts = [str(h) for h in runtime.get("allow_net") or []]
        if hosts:
            flags.append("--allow-net=" + ",".join(hosts))
        return flags

    async def _deno(self, skill: Skill, path: Path, runtime: dict, env: dict) -> tuple[list[str], dict]:
        if not self._deno_bin:
            raise ScriptError("JavaScript and TypeScript scripts need Deno, which was not found. Install Deno (https://deno.com).")
        cache = self.work_dir / "deno-cache"
        cache.mkdir(parents=True, exist_ok=True)
        env = {**env, "DENO_DIR": str(cache), "DENO_NO_UPDATE_CHECK": "1"}
        flags = self.deno_flags(skill, runtime, cache)
        if runtime.get("npm"):
            # Fetch declared packages now (this runs no skill code); the run itself then works from the cache.
            await self._setup([self._deno_bin, "cache", "--no-lock", str(path)], "download the packages", env)
            flags.append("--cached-only")
        return [self._deno_bin, "run", *flags, str(path)], env

    # -- process handling --------------------------------------------------------------------

    async def _setup(self, command: list[str], what: str, env: dict | None = None) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env or _base_env(),
            )
            _, err = await asyncio.wait_for(proc.communicate(), SETUP_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            raise ScriptError(f"Timed out while trying to {what}.") from None
        except OSError as e:
            raise ScriptError(f"Could not start the tool needed to {what}: {e}") from None
        if proc.returncode != 0:
            raise ScriptError(f"Could not {what}: {_last_lines(err)}")

    async def _execute(self, command: list[str], env: dict, cwd: str, payload: bytes, timeout: float) -> bytes:
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        except OSError as e:
            raise ScriptError(f"Could not start the script: {e}") from None

        async def feed() -> None:
            try:
                proc.stdin.write(payload)
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass  # the script exited without reading its input

        async def capped(stream, limit: int) -> tuple[bytes, bool]:
            data = bytearray()
            while chunk := await stream.read(65536):
                data += chunk
                if len(data) > limit:
                    proc.kill()  # fail closed: a runaway script is stopped, not truncated and trusted
                    return bytes(data[:limit]), True
            return bytes(data), False

        async def work():
            return await asyncio.gather(feed(), capped(proc.stdout, MAX_OUTPUT_BYTES), capped(proc.stderr, MAX_STDERR_BYTES))

        try:
            _, (out, too_big), (err, _) = await asyncio.wait_for(work(), timeout)
            await proc.wait()
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ScriptError(f"The script ran longer than {timeout:g} seconds and was stopped.") from None
        if too_big:
            raise ScriptError(f"The script printed more than {MAX_OUTPUT_BYTES // 1000} KB and was stopped.")
        if proc.returncode != 0:
            raise ScriptError(f"The script failed (exit {proc.returncode}). {_last_lines(err)}".strip())
        return out
