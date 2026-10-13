"""The MCP servers the user added (`mcp_servers.json` in the data dir; never any secret values).

Only the user adds, edits or enables a server, through the API the app calls; no tool, skill or model can.
Each tool a server offers is pinned by a hash of its definition (name, description, input schema,
annotations). Tools are offered to the model only once the user approved that exact definition; a new or
changed definition waits in `changed` until the user reviews it, and a changed tool is off meanwhile.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from piyo.config import data_dir
from piyo.config.protected import broad_reason, protected_reason

NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
MAX_ARGS = 40
MAX_ARG_CHARS = 1000
MAX_ENV = 40
MAX_SERVERS = 50


class McpConfigError(ValueError):
    """A server setting that is not valid; the message is shown to the user."""


@dataclass
class McpTool:
    name: str  # the server's own name for it
    description: str
    input_schema: dict
    annotations: dict | None
    hash: str
    auto: bool = False  # the user lets it run without asking (never for one the server calls destructive)

    @property
    def destructive(self) -> bool:
        # Hints are the server's word, so they only add caution: one that says it destroys things can never
        # run without asking; one that says nothing (or "read only") is the user's call.
        return (self.annotations or {}).get("destructive_hint") is True

    @property
    def read_only(self) -> bool:
        return (self.annotations or {}).get("read_only_hint") is True


def tool_hash(name: str, description: str, input_schema: dict, annotations: dict | None) -> str:
    raw = json.dumps([name, description, input_schema, annotations], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class McpServer:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)  # plain settings; keys go in `secrets`
    secrets: list[str] = field(default_factory=list)  # environment names whose values are in the keychain
    enabled: bool = True
    always_on: bool = False  # its approved tools are offered in every run, not only through a skill
    # An `npx` server runs in Deno's sandbox (deno.py): what the user lets it reach beyond its own folder.
    allow_net: list[str] = field(default_factory=list)  # hosts, or "*" for any
    allow_read: list[str] = field(default_factory=list)  # absolute folders or files
    allow_write: list[str] = field(default_factory=list)
    npm_version: str | None = (
        None  # the package version pinned at its first start; runs until the user updates
    )
    tools: dict[str, McpTool] = field(default_factory=dict)  # approved definitions
    changed: dict[str, McpTool] = field(default_factory=dict)  # new or changed, waiting for the user

    def approved(self) -> list[McpTool]:
        """The tools that may be offered: approved and not changed since."""
        return [t for name, t in sorted(self.tools.items()) if name not in self.changed]


def secret_key(server: str, name: str) -> str:
    """The keychain entry for one of a server's secrets."""
    return f"mcp.{server}.{name}"


HOST = re.compile(r"(?:\*|[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::\d{1,5})?)")
MAX_ALLOWED = 30


def path_problem(raw: str) -> str | None:
    """Why a folder cannot be given to a server, or None. The same limits as approving a folder for Piyo."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return f"{raw!r} must be a full path."
    resolved = path.resolve()
    return protected_reason(resolved) or broad_reason(resolved)


def check_server(server: McpServer) -> None:
    if not NAME.fullmatch(server.name):
        raise McpConfigError("The name must be 1-32 lowercase letters, digits or dashes (like 'github').")
    if not server.command.strip() or len(server.command) > MAX_ARG_CHARS:
        raise McpConfigError("Give the command that starts the server (like 'npx' or 'uvx').")
    if len(server.args) > MAX_ARGS or any(len(a) > MAX_ARG_CHARS for a in server.args):
        raise McpConfigError(f"At most {MAX_ARGS} arguments of {MAX_ARG_CHARS} characters each.")
    if len(server.env) + len(server.secrets) > MAX_ENV:
        raise McpConfigError(f"At most {MAX_ENV} environment settings.")
    for key in [*server.env, *server.secrets]:
        if not ENV_NAME.fullmatch(key):
            raise McpConfigError(f"{key!r} is not a valid environment variable name.")
    if set(server.env) & set(server.secrets):
        raise McpConfigError("A name cannot be both a plain setting and a secret.")
    if any(len(v) > MAX_ARG_CHARS for v in server.env.values()):
        raise McpConfigError(f"Environment values are at most {MAX_ARG_CHARS} characters.")
    if max(len(server.allow_net), len(server.allow_read), len(server.allow_write)) > MAX_ALLOWED:
        raise McpConfigError(f"At most {MAX_ALLOWED} sites or folders of each kind.")
    for host in server.allow_net:
        if not HOST.fullmatch(host):
            raise McpConfigError(f"{host!r} is not a site name like example.com.")
    for raw in [*server.allow_read, *server.allow_write]:
        if problem := path_problem(raw):
            raise McpConfigError(f"{raw}: {problem}")


def _tool(raw: dict) -> McpTool:
    return McpTool(
        name=str(raw["name"]),
        description=str(raw.get("description") or ""),
        input_schema=raw.get("input_schema") if isinstance(raw.get("input_schema"), dict) else {},
        annotations=raw.get("annotations") if isinstance(raw.get("annotations"), dict) else None,
        hash=str(raw["hash"]),
        auto=raw.get("auto") is True,
    )


def _server(raw: dict) -> McpServer:
    server = McpServer(
        name=str(raw["name"]),
        command=str(raw["command"]),
        args=[str(a) for a in raw.get("args") or []],
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
        secrets=[str(s) for s in raw.get("secrets") or []],
        enabled=raw.get("enabled") is not False,
        always_on=raw.get("always_on") is True,
        allow_net=[str(h) for h in raw.get("allow_net") or []],
        allow_read=[str(h) for h in raw.get("allow_read") or []],
        allow_write=[str(h) for h in raw.get("allow_write") or []],
        npm_version=str(raw["npm_version"]) if raw.get("npm_version") else None,
        tools={str(k): _tool(v) for k, v in (raw.get("tools") or {}).items()},
        changed={str(k): _tool(v) for k, v in (raw.get("changed") or {}).items()},
    )
    check_server(server)
    return server


class McpServers:
    filename = "mcp_servers.json"

    def list(self) -> list[McpServer]:
        """Saved servers; an entry that does not read back is skipped, not trusted."""
        try:
            raw = json.loads((data_dir() / self.filename).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out = []
        for item in raw.get("servers", []) if isinstance(raw, dict) else []:
            try:
                out.append(_server(item))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
        return out

    def get(self, name: str) -> McpServer:
        for server in self.list():
            if server.name == name:
                return server
        raise KeyError(name)

    def _write(self, servers: list[McpServer]) -> None:
        path = data_dir() / self.filename
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"servers": [asdict(s) for s in servers]}, indent=1), encoding="utf-8")
        tmp.replace(path)

    def add(self, server: McpServer) -> McpServer:
        check_server(server)
        servers = self.list()
        if any(s.name == server.name for s in servers):
            raise McpConfigError(f"There is already a server called {server.name!r}.")
        if len(servers) >= MAX_SERVERS:
            raise McpConfigError(f"At most {MAX_SERVERS} servers.")
        self._write([*servers, server])
        return server

    def save(self, server: McpServer) -> McpServer:
        check_server(server)
        servers = self.list()
        if not any(s.name == server.name for s in servers):
            raise KeyError(server.name)
        self._write([server if s.name == server.name else s for s in servers])
        return server

    def remove(self, name: str) -> None:
        servers = self.list()
        if not any(s.name == name for s in servers):
            raise KeyError(name)
        self._write([s for s in servers if s.name != name])
