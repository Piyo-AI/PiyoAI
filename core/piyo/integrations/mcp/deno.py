"""`npx` MCP servers run in Deno's permission sandbox instead of Node (owner's decision, 2026-10-13).

`npx -y <package> <args>` becomes `deno run --no-prompt <permissions> npm:<package>@<version> <args>` with
Piyo's own Deno (`piyo.skills.helper_tools`), so no Node.js is needed and the server can only reach what the
user allowed:

- Always: environment variables (the process gets only the SDK's short safe list plus the server's own
  settings and secrets), reading the Deno package cache (its own code), and reading and writing its own
  folder (`<data>/mcp/<server>/`, also its working directory).
- Only when the user allowed them for that server: sites (`allow_net`), more folders to read (`allow_read`)
  and to write (`allow_write`). Never: starting programs, native libraries.

Tried with five popular servers (2026-10-13): sequential-thinking, memory and everything work with the
baseline; filesystem needs its folders allowed; context7 needs its site. When Deno refuses something it says
so ("Requires net access to ..."), in the server's output or in a tool's result; `blocked_requests` picks
that up so Settings can offer to allow it.

The package version is pinned the first time the server starts (looked up on the npm registry), so later
starts run the code the user reviewed the tools of, not whatever was published since. A version in the
arguments is used as given.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import quote

import httpx

from piyo.integrations.mcp.client import McpError
from piyo.integrations.mcp.config import McpServer

NPX_FLAGS = {"-y", "--yes", "-q", "--quiet", "--"}
PACKAGE = re.compile(r"(@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*(@[\w.^~<>=*+-]+)?", re.I)
BLOCKED = re.compile(r'Requires (net|read|write|env|run|sys|ffi|import) access to "([^"]+)"')
REGISTRY = "https://registry.npmjs.org"

VersionLookup = Callable[[str], Awaitable[str]]


def split_npx(args: list[str]) -> tuple[str, str | None, list[str]]:
    """`npx` arguments -> (package name, version or None, the server's own arguments)."""
    rest = list(args)
    while rest and rest[0] in NPX_FLAGS:
        rest.pop(0)
    if not rest or rest[0].startswith("-"):
        raise McpError(
            "Piyo runs npx servers in Deno and understands 'npx -y <package> <arguments>'. Put the package "
            "name "
            "first (options like --package are not supported)."
        )
    spec, server_args = rest[0], rest[1:]
    if not PACKAGE.fullmatch(spec):
        raise McpError(f"{spec!r} does not look like an npm package name.")
    at = spec.rfind("@")
    if at > 0:  # "@scope/name" starts with @; a version comes after a later one
        return spec[:at], spec[at + 1 :], server_args
    return spec, None, server_args


async def npm_latest(package: str) -> str:
    """The version `npx` would run today, from the public npm registry."""
    url = f"{REGISTRY}/{quote(package, safe='@')}/latest"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(url)
            response.raise_for_status()
            version = response.json().get("version")
    except (httpx.HTTPError, ValueError, AttributeError) as e:
        raise McpError(f"Piyo could not look up the npm package {package!r}: {e}") from None
    if not isinstance(version, str) or not version:
        raise McpError(f"The npm registry has no version of {package!r}.")
    return version


def deno_args(
    server: McpServer, package: str, version: str, server_args: list[str], cache: Path, home: Path
) -> list[str]:
    """`deno run` arguments: the permissions are the baseline plus exactly what the user allowed."""
    reads = [
        str(cache),
        str(home),
        *server.allow_read,
        *server.allow_write,
    ]  # writing a file means reading it too
    writes = [str(home), *server.allow_write]
    flags = [
        "run",
        "--no-prompt",
        "--no-lock",
        "--no-config",
        "--allow-env",
        f"--allow-read={','.join(reads)}",
        f"--allow-write={','.join(writes)}",
    ]
    if "*" in server.allow_net:
        flags.append("--allow-net")
    elif server.allow_net:
        flags.append(f"--allow-net={','.join(server.allow_net)}")
    return [*flags, f"npm:{package}@{version}", *server_args]


def blocked_requests(text: str) -> list[tuple[str, str]]:
    """What Deno refused, as (kind, target): ("net", "context7.com"), ("read", "C:\\data")."""
    found = []
    for kind, target in BLOCKED.findall(text):
        if kind == "net":
            target = target.rsplit(":", 1)[0] if re.search(r":\d+$", target) else target
        found.append((kind, target))
    return list(dict.fromkeys(found))
