"""MCP tools as Piyo tools, and the manager that keeps the tool registry in step with the user's servers.

- Names: `mcp.<server>.<tool>` (wire `mcp__<server>__<tool>`): the tool part keeps letters, digits, `-` and
  single `_`, so the wire name maps back unambiguously and stays within 64 characters; clashes get `-2`,
  `-3`, ... in sorted order.
- Risk: every MCP tool asks (`confirm`) unless the user marked it auto, and never for one its server calls
  destructive (invariant 1: a tool property, never a skill's or a server's).
- Visibility: like any tool, only once a loaded skill grants it (`mcp.<server>.<tool>` or `mcp.<server>.*`),
  or in every run for a server the user switched "always on".
- Definitions and results are untrusted text: descriptions are capped and labelled as the server's, results
  are fenced, capped and mark the run as having read private data and outside text (`safety/exfil.py`,
  `taint.py`).
- Approved definitions are registered at startup from the saved pins, without starting anything. The first
  call after a server starts lists its tools again; a definition that changed is switched off until the user
  reviews it.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from mcp import StdioServerParameters
from mcp import types as mt

from piyo.integrations.mcp.client import McpClient, McpError
from piyo.integrations.mcp.config import McpServer, McpServers, McpTool, secret_key, tool_hash
from piyo.integrations.mcp.deno import VersionLookup, blocked_requests, deno_args, npm_latest, split_npx
from piyo.safety.untrusted import wrap_untrusted
from piyo.tools.base import Risk, RunContext, Tool, ToolRegistry

PREFIX = "mcp."
WIRE_MAX = 64
MAX_DESCRIPTION = 1000
MAX_SCHEMA_BYTES = 16_000
MAX_RESULT_CHARS = 20_000
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 4_000_000  # base64 characters
ALWAYS_ON_MAX_TOOLS = 20  # an always-on server's tools go into every prompt


def tool_names(server: str, names: list[str]) -> dict[str, str]:
    """The server's tool names -> Piyo's internal names; deterministic for the same set."""
    room = WIRE_MAX - len(f"mcp__{server}__")
    out: dict[str, str] = {}
    taken: set[str] = set()
    for name in sorted(names):
        base = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_-]", "_", name)).strip("_") or "tool"
        candidate, n = base[:room], 1
        while candidate in taken:
            n += 1
            suffix = f"-{n}"
            candidate = base[: room - len(suffix)] + suffix
        taken.add(candidate)
        out[name] = f"{PREFIX}{server}.{candidate}"
    return out


def definition(tool: mt.Tool) -> McpTool | None:
    """A listed tool as a definition to pin, or None when it is too large to offer."""
    schema = tool.input_schema if isinstance(tool.input_schema, dict) else {}
    if len(json.dumps(schema)) > MAX_SCHEMA_BYTES:
        return None
    annotations = tool.annotations.model_dump(exclude_none=True) if tool.annotations else None
    description = (tool.description or tool.title or "")[:MAX_DESCRIPTION]
    return McpTool(
        tool.name, description, schema, annotations, tool_hash(tool.name, description, schema, annotations)
    )


def result_text(result: mt.CallToolResult, ctx: RunContext) -> str:
    parts: list[str] = []
    images = 0
    for block in result.content:
        if isinstance(block, mt.TextContent):
            parts.append(block.text)
        elif isinstance(block, mt.ImageContent):
            if (
                images < MAX_IMAGES
                and len(block.data) <= MAX_IMAGE_BYTES
                and block.mime_type.startswith("image/")
            ):
                ctx.attach_image(block.data, block.mime_type)
                images += 1
                parts.append("[an image, attached]")
            else:
                parts.append("[an image, left out]")
        elif isinstance(block, mt.EmbeddedResource) and isinstance(block.resource, mt.TextResourceContents):
            parts.append(block.resource.text)
        elif isinstance(block, mt.ResourceLink):
            parts.append(f"[resource {block.uri}]")
        else:
            parts.append(f"[{getattr(block, 'type', 'content')} left out]")
    if not parts and result.structured_content is not None:
        parts.append(json.dumps(result.structured_content, ensure_ascii=False, indent=1))
    text = "\n".join(parts).strip() or "(no output)"
    return text[:MAX_RESULT_CHARS] + (
        "\n[cut: the result was longer]" if len(text) > MAX_RESULT_CHARS else ""
    )


SecretGetter = Callable[[str], str | None]
# "uv" or "deno" -> its path (Piyo's helper download, piyo.skills.helper_tools)
ProgramFinder = Callable[[str], Awaitable[str]]


class McpManager:
    def __init__(
        self,
        store: McpServers,
        registry: ToolRegistry,
        get_secret: SecretGetter,
        program: ProgramFinder,
        work_dir: Path,
        launch_override: Callable[[McpServer], Awaitable[StdioServerParameters]] | None = None,
        npm_version: VersionLookup = npm_latest,
    ) -> None:
        self.store, self.registry, self._get_secret, self._program = store, registry, get_secret, program
        self.work_dir = work_dir
        self._launch_override = launch_override  # tests
        self._npm_version = npm_version
        self._blocked: dict[str, list[tuple[str, str]]] = {}  # what Deno refused, seen in results
        self.clients: dict[str, McpClient] = {}
        self._registered: dict[str, list[str]] = {}  # server -> internal tool names in the registry

    # -- processes ---------------------------------------------------------------------------

    async def launch_params(self, server: McpServer) -> StdioServerParameters:
        if self._launch_override:
            return await self._launch_override(server)
        command, args = server.command, list(server.args)
        home = self.work_dir / server.name
        home.mkdir(parents=True, exist_ok=True)
        env = {"NO_COLOR": "1"}
        if command in ("uvx", "uv"):  # Piyo's own uv, downloaded the first time it is needed
            uv = await self._program("uv")
            command, args = uv, (["tool", "run", *args] if server.command == "uvx" else args)
        elif command == "npx":  # in Deno's sandbox (deno.py), with Piyo's own Deno
            package, version, server_args = split_npx(args)
            version = version or server.npm_version or await self._pin(server.name, package)
            cache = self.work_dir / "deno-cache"
            cache.mkdir(parents=True, exist_ok=True)
            command = await self._program("deno")
            args = deno_args(server, package, version, server_args, cache, home)
            env |= {"DENO_DIR": str(cache), "DENO_NO_UPDATE_CHECK": "1"}
        else:
            found = shutil.which(command)
            if found is None:
                hint = " npx comes with Node.js (nodejs.org)." if command in ("npx", "node", "npm") else ""
                raise McpError(
                    f"Piyo could not find the program {command!r}.{hint} Install it or give its full path."
                )
            command = found
        env |= server.env
        for name in server.secrets:
            if value := self._get_secret(secret_key(server.name, name)):
                env[name] = value
        return StdioServerParameters(command=command, args=args, env=env, cwd=home)

    async def _pin(self, name: str, package: str) -> str:
        """First start of an npx server without a version: run today's version from now on."""
        version = await self._npm_version(package)
        server = self.store.get(name)
        server.npm_version = version
        self.store.save(server)
        return version

    def blocked(self, name: str) -> list[tuple[str, str]]:
        """What Deno refused this server (in its results and output) that the user has not allowed since."""
        try:
            server = self.store.get(name)
        except KeyError:
            return []
        log = "\n".join(self.clients[name].log_tail(200)) if name in self.clients else ""
        allowed = {
            "net": set(server.allow_net),
            "read": {*server.allow_read, *server.allow_write},
            "write": set(server.allow_write),
        }
        found = [*self._blocked.get(name, []), *blocked_requests(log)]
        if "*" in server.allow_net:
            allowed["net"].add("*")
        return [
            b
            for b in dict.fromkeys(found)
            if b[1] not in allowed.get(b[0], set()) and not (b[0] == "net" and "*" in allowed["net"])
        ]

    def _note_blocked(self, name: str, text: str) -> None:
        if found := blocked_requests(text):
            self._blocked[name] = list(dict.fromkeys([*self._blocked.get(name, []), *found]))[-20:]

    def client(self, name: str) -> McpClient:
        if name not in self.clients:

            async def launch() -> StdioServerParameters:
                return await self.launch_params(self.store.get(name))

            self.clients[name] = McpClient(name, launch, self.work_dir / "logs" / f"{name}.log")
        return self.clients[name]

    async def stop(self, name: str) -> None:
        if client := self.clients.get(name):
            await client.stop()

    async def stop_all(self) -> None:
        for client in list(self.clients.values()):
            await client.stop()

    async def forget(self, name: str) -> None:
        """The server was removed: stop it and take its tools away."""
        await self.stop(name)
        self.clients.pop(name, None)
        self._unregister(name)

    # -- tools -------------------------------------------------------------------------------

    async def discover(self, name: str) -> McpServer:
        """Start the server, list its tools, compare with the pins: new or changed ones wait for the user."""
        server = self.store.get(name)
        client = self.client(name)
        listed = await client.list_tools()
        seen: dict[str, McpTool] = {}
        for item in listed:
            if (found := definition(item)) is not None:
                seen[found.name] = found
        server = self.store.get(name)  # fresh: the user may have changed it while it started
        server.changed = {
            n: t for n, t in seen.items() if n not in server.tools or server.tools[n].hash != t.hash
        }
        server.tools = {n: t for n, t in server.tools.items() if n in seen}  # gone from the server: gone here
        self.store.save(server)
        client.verified = True
        self.sync(name)
        return server

    def approve(self, name: str, hashes: dict[str, str]) -> McpServer:
        """The user approved exactly these definitions (by hash: a later change is not approved)."""
        server = self.store.get(name)
        for tool, digest in hashes.items():
            pending = server.changed.get(tool)
            if pending is None or pending.hash != digest:
                raise ValueError(f"{tool!r} changed again or is not waiting for approval; look at it again.")
            server.tools[tool] = pending  # a changed definition starts again as "ask every time"
            del server.changed[tool]
        self.store.save(server)
        self.sync(name)
        return server

    def sync(self, name: str | None = None) -> None:
        """Make the registry hold exactly the approved tools of enabled servers."""
        servers = self.store.list()
        names = [name] if name else sorted({*self._registered, *(s.name for s in servers)})
        by_name = {s.name: s for s in servers}
        for server_name in names:
            self._unregister(server_name)
            server = by_name.get(server_name)
            if server is None or not server.enabled:
                continue
            approved = server.approved()
            internal = tool_names(server.name, [t.name for t in approved])
            always = server.always_on and len(approved) <= ALWAYS_ON_MAX_TOOLS
            for tool in approved:
                self.registry.register(self._tool(server.name, tool, internal[tool.name], always))
            self._registered[server.name] = list(internal.values())

    def _unregister(self, name: str) -> None:
        for tool in self._registered.pop(name, []):
            self.registry.unregister(tool)

    def _tool(self, server: str, tool: McpTool, internal: str, always_on: bool) -> Tool:
        def current() -> McpTool | None:
            try:
                found = self.store.get(server)
            except KeyError:
                return None
            return found.tools.get(tool.name) if tool.name not in found.changed else None

        def risk_for(args: dict) -> Risk:
            now = current()  # the user's choice as it is now, never as it was when the tool was registered
            return Risk.AUTO if now is not None and now.auto and not now.destructive else Risk.CONFIRM

        async def handler(args: dict, ctx: RunContext) -> str:
            client = self.client(server)
            if not client.verified:
                await self.discover(server)  # first call since it started: check the pins
            now = current()
            if now is None or now.hash != tool.hash:
                raise McpError(
                    f"The MCP server {server!r} changed {tool.name!r} since the user approved it, so it is "
                    "off "
                    "until they review it in Settings > MCP servers. Tell the user."
                )
            try:
                result = await client.call(tool.name, args)
            except McpError as e:
                self._note_blocked(server, str(e))
                raise
            text = result_text(result, ctx)
            self._note_blocked(server, text)
            fenced = wrap_untrusted(text, f"MCP server {server}, tool {tool.name}")
            if result.is_error:  # the server's own words, so fenced like a result
                raise McpError(f"The MCP server {server!r} reported an error: {fenced}")
            return fenced

        def summarize(args: dict) -> str:
            shown = json.dumps(args, ensure_ascii=False)[:300]
            return f"Use {tool.name} on the MCP server {server!r} with {shown}"

        return Tool(
            name=internal,
            description=f"[From the MCP server '{server}'; its own description follows] {tool.description}",
            parameters={"type": "object", **tool.input_schema}
            if tool.input_schema.get("type") in (None, "object")
            else {"type": "object", "properties": {}},
            handler=handler,
            risk=Risk.CONFIRM,
            risk_for=risk_for,
            summarize=summarize,
            core=always_on,
        )
