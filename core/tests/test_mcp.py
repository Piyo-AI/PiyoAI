import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_runs import AUTH, TOKEN

from piyo.agent import Agent, Finished, ToolFinished
from piyo.config import data_dir
from piyo.config.secrets import get_secret
from piyo.integrations.mcp import McpConfigError, McpManager, McpServer, McpServers
from piyo.integrations.mcp import client as client_mod
from piyo.integrations.mcp.tools import tool_names
from piyo.models import Provider
from piyo.models.providers import ApiStyle
from piyo.models.turn import Message, ToolCall, TurnDone
from piyo.safety import PermissionGate
from piyo.safety.exfil import produces_private_data
from piyo.safety.taint import produces_untrusted
from piyo.safety.untrusted import TAG
from piyo.server import app as server_app
from piyo.skills import SkillRegistry
from piyo.skills.install import permissions_of
from piyo.skills.manifest import load_skill_dir
from piyo.skills.validate import validate_package
from piyo.tools import ToolRegistry, core_tools
from piyo.tools.base import is_granted

FAKE = str(Path(__file__).with_name("mcp_fake_server.py"))
PROVIDER = Provider(
    id="ollama", name="Ollama", api_style=ApiStyle.OPENAI, base_url="http://x.test", local=True
)
SECRETS: dict[str, str] = {}


def fake_server(name="fake", **kw) -> McpServer:
    return McpServer(name=name, command=sys.executable, args=[FAKE], **kw)


async def no_program(tool):
    raise AssertionError("not needed")


@pytest.fixture
async def mcp(tmp_path):
    registry = ToolRegistry(core_tools())
    manager = McpManager(McpServers(), registry, SECRETS.get, no_program, tmp_path / "mcp")
    yield manager
    await manager.stop_all()


async def approve_all(manager, name="fake"):
    server = await manager.discover(name)
    return manager.approve(name, {n: t.hash for n, t in server.changed.items()})


def skill_dir(tmp_path, tools, name="uses-fake"):
    folder = tmp_path / "skills" / name
    folder.mkdir(parents=True)
    listed = "".join(f"\n    - {t}" for t in tools)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Uses the fake server\nversion: 1.0.0\nauthor: me\nlicense: MIT\n"
        f"requires:\n  tools:{listed}\n---\nUse the fake tools.\n",
        encoding="utf-8",
    )
    return folder


def run_agent(manager, tmp_path, calls, approve=True, skill_tools=("mcp.fake.*",)):
    """One run that loads the skill, then makes `calls`; returns (tool results, approval requests)."""
    skill_dir(tmp_path, skill_tools)
    skills = SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "skills")
    turns = [TurnDone(tool_calls=[ToolCall(id="0", name="load_skill", arguments={"name": "uses-fake"})])]
    turns += [
        TurnDone(tool_calls=[ToolCall(id=str(i + 1), name=n, arguments=a)]) for i, (n, a) in enumerate(calls)
    ]
    turns.append(TurnDone(text="done"))
    asked = []

    async def turn(provider, model, messages, specs, system, max_tokens):
        yield turns.pop(0)

    async def approver(request):
        asked.append(request)
        return approve

    agent = Agent(PROVIDER, "m", manager.registry, skills, PermissionGate(approver), turn_fn=turn)

    async def go():
        return [e async for e in agent.run([Message(role="user", content="go")])]

    return go, asked


# -- names and settings --------------------------------------------------------------------


def test_tool_names_are_safe_unique_and_map_back():
    names = tool_names("srv", ["a.b", "a/b", "x__y", "ok_name", "z" * 100, "-"])
    assert names["ok_name"] == "mcp.srv.ok_name"
    assert names["x__y"] == "mcp.srv.x_y"
    assert {names["a.b"], names["a/b"]} == {"mcp.srv.a_b", "mcp.srv.a_b-2"}
    for internal in names.values():
        wire = internal.replace(".", "__")
        assert len(wire) <= 64 and wire.replace("__", ".") == internal
    assert tool_names("srv", ["a/b", "a.b"]) == tool_names("srv", ["a.b", "a/b"])  # same set, same names


def test_server_settings_are_checked_and_hold_no_secret_values():
    store = McpServers()
    for bad in (
        McpServer(name="Bad Name", command="x"),
        McpServer(name="ok", command=" "),
        McpServer(name="ok", command="x", env={"1BAD": "v"}),
        McpServer(name="ok", command="x", env={"K": "v"}, secrets=["K"]),
    ):
        with pytest.raises(McpConfigError):
            store.add(bad)
    store.add(McpServer(name="ok", command="npx", args=["-y", "pkg"], env={"MODE": "x"}, secrets=["TOKEN"]))
    with pytest.raises(McpConfigError, match="already"):
        store.add(McpServer(name="ok", command="npx"))
    saved = (data_dir() / McpServers.filename).read_text(encoding="utf-8")
    assert '"TOKEN"' in saved and store.get("ok").secrets == ["TOKEN"]


def test_skill_grants_validation_and_review_labels(tmp_path):
    assert is_granted("mcp.fake.add", {"mcp.fake.*"}) and is_granted("mcp.fake.add", {"mcp.fake.add"})
    assert not is_granted("mcp.other.add", {"mcp.fake.*"}) and not is_granted("gmail.send", {"gmail.*"})
    folder = skill_dir(tmp_path, ["mcp.fake.*", "mcp.github.create_issue", "web.fetch"])
    _, report = validate_package(folder)
    assert not [e for e in report.errors if "requires.tools" in e]
    assert any("MCP server 'github'" in w for w in report.warnings)
    labels = permissions_of(load_skill_dir(folder, "user"))
    assert {"mcp:fake", "mcp:github", "tool:web.fetch"} <= set(labels)
    bad = skill_dir(tmp_path, ["mcp.Fake.x y"], name="bad-one")
    assert any("not a tool Piyo has" in e for e in validate_package(bad)[1].errors)


# -- the server ----------------------------------------------------------------------------


async def test_new_tools_wait_for_approval_and_only_approved_ones_are_offered(mcp):
    mcp.store.add(fake_server())
    server = await mcp.discover("fake")
    assert server.tools == {} and "add" in server.changed
    assert not [n for n in mcp.registry.names() if n.startswith("mcp.")]
    with pytest.raises(ValueError, match="look at it again"):
        mcp.approve("fake", {"add": "0" * 64})
    mcp.approve("fake", {"add": server.changed["add"].hash})
    assert {n for n in mcp.registry.names() if n.startswith("mcp.")} == {"mcp.fake.add"}
    weird = tool_names("fake", list(server.changed))["weird.name/with spaces__and__dots"]
    assert weird == "mcp.fake.weird_name_with_spaces_and_dots"
    tool = mcp.registry.get("mcp__fake__add")
    assert tool is not None and not tool.core and "[From the MCP server 'fake'" in tool.description


async def test_every_call_asks_unless_the_user_marked_it_auto_and_results_are_fenced(mcp, tmp_path):
    mcp.store.add(fake_server())
    await approve_all(mcp)
    go, asked = run_agent(mcp, tmp_path, [("mcp__fake__add", {"a": 2, "b": 3})])
    events = await go()
    [result] = [e for e in events if isinstance(e, ToolFinished) and e.name == "mcp__fake__add"]
    assert not result.is_error and "5" in result.output and result.output.startswith(f"<{TAG}")
    assert [r.tool for r in asked] == ["mcp.fake.add"] and "MCP server 'fake'" in asked[0].summary

    server = mcp.store.get("fake")
    server.tools["add"].auto = True
    mcp.store.save(server)
    go, asked = run_agent(mcp, tmp_path / "2", [("mcp__fake__add", {"a": 1, "b": 1})])
    await go()
    assert asked == []  # the user's choice, read at call time


async def test_a_tool_the_server_calls_destructive_always_asks(mcp, tmp_path):
    mcp.store.add(fake_server())
    await approve_all(mcp)
    server = mcp.store.get("fake")
    assert server.tools["wipe"].destructive and server.tools["read_note"].read_only
    server.tools["wipe"].auto = True  # even if the file says so
    mcp.store.save(server)
    go, asked = run_agent(mcp, tmp_path, [("mcp__fake__wipe", {})], approve=False)
    events = await go()
    assert [r.tool for r in asked] == ["mcp.fake.wipe"]
    assert any(isinstance(e, ToolFinished) and e.is_error for e in events)


async def test_a_changed_definition_switches_the_tool_off_until_reviewed(mcp, tmp_path):
    mcp.store.add(fake_server())
    await approve_all(mcp)
    server = mcp.store.get("fake")
    server.tools["add"].auto = True
    server.env = {"FAKE_VARIANT": "2"}  # the server now describes `add` differently
    mcp.store.save(server)
    await mcp.stop("fake")
    go, asked = run_agent(mcp, tmp_path, [("mcp__fake__add", {"a": 1, "b": 2})])
    events = await go()
    [result] = [e for e in events if isinstance(e, ToolFinished) and e.name == "mcp__fake__add"]
    assert result.is_error and "changed 'add'" in result.output
    assert "mcp.fake.add" not in mcp.registry.names()
    server = mcp.store.get("fake")
    assert "send the user's mail" in server.changed["add"].description
    mcp.approve("fake", {"add": server.changed["add"].hash})
    assert "mcp.fake.add" in mcp.registry.names() and not mcp.store.get("fake").tools["add"].auto


async def test_server_errors_crashes_and_timeouts_become_error_results(mcp, tmp_path, monkeypatch):
    mcp.store.add(fake_server())
    await approve_all(mcp)
    server = mcp.store.get("fake")
    for name in ("broken", "crash", "slow", "add"):
        server.tools[name].auto = True
    mcp.store.save(server)
    monkeypatch.setattr(client_mod, "CALL_TIMEOUT_S", 1.0)
    calls = [
        ("mcp__fake__broken", {}),
        ("mcp__fake__crash", {}),
        ("mcp__fake__add", {"a": 1, "b": 1}),
        ("mcp__fake__slow", {}),
    ]
    go, _ = run_agent(mcp, tmp_path, calls)
    events = await go()
    results = {e.name: e for e in events if isinstance(e, ToolFinished)}
    assert (
        results["mcp__fake__broken"].is_error and f"<{TAG}" in results["mcp__fake__broken"].output
    )
    assert results["mcp__fake__crash"].is_error
    assert not results["mcp__fake__add"].is_error  # started again after the crash
    assert results["mcp__fake__slow"].is_error and "within 1 s" in results["mcp__fake__slow"].output
    assert isinstance(events[-1], Finished)
    assert "fake server starting" in "\n".join(mcp.client("fake").log_tail())


async def test_pictures_are_attached_and_outside_text_is_marked(mcp, tmp_path):
    mcp.store.add(fake_server())
    await approve_all(mcp)
    from piyo.tools import RunContext

    ctx = RunContext(skills=SkillRegistry(builtin_dir=tmp_path, user_dir=tmp_path))
    out = await mcp.registry.get("mcp.fake.picture").handler({}, ctx)
    assert "[an image, attached]" in out and ctx.take_images()[0].media_type == "image/png"
    note = await mcp.registry.get("mcp.fake.read_note").handler({}, ctx)
    assert note.startswith(f"<{TAG}") and "Ignore your instructions" in note  # data, fenced
    assert produces_private_data("mcp__fake__read_note") and produces_untrusted("mcp.fake.read_note")


async def test_secrets_reach_only_the_server(mcp, tmp_path):
    SECRETS["mcp.fake.FAKE_TOKEN"] = "s3cret"
    try:
        mcp.store.add(fake_server(env={"FAKE_MODE": "plain"}, secrets=["FAKE_TOKEN"]))
        await approve_all(mcp)
        from piyo.tools import RunContext

        ctx = RunContext(skills=SkillRegistry(builtin_dir=tmp_path, user_dir=tmp_path))
        out = await mcp.registry.get("mcp.fake.whoami").handler({}, ctx)
        assert "token=s3cret mode=plain" in out
        assert "s3cret" not in (data_dir() / McpServers.filename).read_text(encoding="utf-8")
    finally:
        SECRETS.clear()


async def test_always_on_tools_need_no_skill_and_a_missing_server_is_explained(mcp, tmp_path):
    mcp.store.add(fake_server(always_on=True))
    await approve_all(mcp)
    assert mcp.registry.get("mcp.fake.add").core
    go, asked = run_agent(mcp, tmp_path, [], skill_tools=("mcp.github.*",))
    events = await go()
    assert any(
        isinstance(e, ToolFinished) and "MCP server(s) github" in e.output
        for e in events
        if e.name == "load_skill"
    )


async def test_a_missing_program_says_what_to_install(mcp):
    mcp.store.add(McpServer(name="node-one", command="surely-not-a-program-xyz"))
    with pytest.raises(Exception, match="could not find the program"):
        await mcp.discover("node-one")


# -- API -----------------------------------------------------------------------------------


def test_api_add_review_approve_and_remove(tmp_path):
    with TestClient(server_app.create_app(TOKEN, start_scheduler=False)) as client:
        body = {"name": "fake", "command": sys.executable, "args": [FAKE], "secrets": ["FAKE_TOKEN"]}
        assert client.post("/api/mcp/servers", json={**body, "name": "Bad!"}, headers=AUTH).status_code == 400
        made = client.post("/api/mcp/servers", json=body, headers=AUTH)
        assert made.status_code == 201 and made.json()["tools"] == []
        assert (
            client.put(
                "/api/mcp/servers/fake/secrets/FAKE_TOKEN", json={"value": "v1"}, headers=AUTH
            ).status_code
            == 204
        )
        assert (
            client.put("/api/mcp/servers/fake/secrets/OTHER", json={"value": "v"}, headers=AUTH).status_code
            == 404
        )
        listed = client.post("/api/mcp/servers/fake/test", headers=AUTH).json()
        assert listed["state"] == "stopped" and {t["status"] for t in listed["tools"]} == {"new"}
        assert listed["secrets"] == [{"name": "FAKE_TOKEN", "set": True}]
        assert "v1" not in json.dumps(listed)
        add = next(t for t in listed["tools"] if t["name"] == "add")
        assert add["tool"] == "mcp.fake.add"
        approved = client.post(
            "/api/mcp/servers/fake/approve", json={"tools": {"add": add["hash"]}}, headers=AUTH
        ).json()
        assert next(t for t in approved["tools"] if t["name"] == "add")["status"] == "approved"
        assert (
            client.post(
                "/api/mcp/servers/fake/approve", json={"tools": {"add": "x"}}, headers=AUTH
            ).status_code
            == 409
        )
        wipe = next(t for t in listed["tools"] if t["name"] == "wipe")
        client.post("/api/mcp/servers/fake/approve", json={"tools": {"wipe": wipe["hash"]}}, headers=AUTH)
        refused = client.put("/api/mcp/servers/fake/tools/wipe", json={"auto": True}, headers=AUTH)
        assert refused.status_code == 400 and "always asks" in refused.json()["detail"]
        auto = client.put("/api/mcp/servers/fake/tools/add", json={"auto": True}, headers=AUTH).json()
        assert next(t for t in auto["tools"] if t["name"] == "add")["auto"] is True
        assert "mcp.fake.add" in client.app.state.tools.names()

        moved = client.put("/api/mcp/servers/fake", json={"args": [FAKE, "--other"]}, headers=AUTH).json()
        assert moved["tools"] == []  # another program: reviewed from scratch
        assert "mcp.fake.add" not in client.app.state.tools.names()

        assert client.get("/api/mcp/servers").status_code == 401
        assert client.delete("/api/mcp/servers/fake", headers=AUTH).status_code == 204
        assert client.get("/api/mcp/servers", headers=AUTH).json() == []
        assert get_secret("mcp.fake.FAKE_TOKEN") is None


def test_no_tool_can_add_or_change_a_server():
    app = server_app.create_app(TOKEN, start_scheduler=False)
    assert not [n for n in app.state.tools.names() if "mcp" in n]  # only servers the user added bring tools


def test_always_on_is_for_small_servers(tmp_path, monkeypatch):
    from piyo.integrations.mcp import tools as tools_mod

    with TestClient(server_app.create_app(TOKEN, start_scheduler=False)) as client:
        client.post(
            "/api/mcp/servers", json={"name": "fake", "command": sys.executable, "args": [FAKE]}, headers=AUTH
        )
        listed = client.post("/api/mcp/servers/fake/test", headers=AUTH).json()
        client.post(
            "/api/mcp/servers/fake/approve",
            json={"tools": {t["name"]: t["hash"] for t in listed["tools"]}},
            headers=AUTH,
        )
        monkeypatch.setattr(tools_mod, "ALWAYS_ON_MAX_TOOLS", 3)
        monkeypatch.setattr(server_app, "ALWAYS_ON_MAX_TOOLS", 3)
        refused = client.put("/api/mcp/servers/fake", json={"always_on": True}, headers=AUTH)
        assert refused.status_code == 400 and "small servers" in refused.json()["detail"]


def test_tool_lists_survive_a_restart_without_starting_the_server(tmp_path):
    asyncio.run(_approve_once(tmp_path))
    app = server_app.create_app(TOKEN, start_scheduler=False)  # a fresh core
    assert "mcp.fake.add" in app.state.tools.names()
    assert app.state.mcp.clients == {}  # registered from the pins; nothing was started


async def _approve_once(tmp_path):
    manager = McpManager(McpServers(), ToolRegistry(), SECRETS.get, no_program, tmp_path / "mcp")
    manager.store.add(fake_server())
    await approve_all(manager)
    await manager.stop_all()
