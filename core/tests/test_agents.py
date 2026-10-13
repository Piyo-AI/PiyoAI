import asyncio
import sqlite3
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_runs import AUTH, TOKEN, scripted

from piyo.agent import Agent, Finished
from piyo.agents import AgentManager, ResourceBroker
from piyo.config.folders import ApprovedFolders, FolderGrant
from piyo.integrations.google import GoogleAuth, GoogleError
from piyo.models import Provider
from piyo.models.providers import ApiStyle
from piyo.models.turn import Message, TextDelta, ToolCall, TurnDone
from piyo.safety import ApprovalDeferred, ApprovalRequest
from piyo.safety.scope import LEGACY, NEW_AGENT, Scope, ScopeError, parse_scope, use_scope
from piyo.scheduler import RunResult, Scheduler, SchedulerStore
from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.tools import Risk, RunContext, ToolRegistry, core_tools
from piyo.tools.agents import agent_tools
from piyo.tools.browser import browser_tools
from piyo.tools.browser.session import BrowserError
from piyo.tools.files import FileAccessError, file_tools


async def allow(request):
    return True


def settle(n: int = 20):
    """Let the event loop run the background tasks a few rounds."""

    async def go():
        for _ in range(n):
            await asyncio.sleep(0)

    return go()


def broker(runs=2, **sizes):
    return ResourceBroker(lambda pool: {"runs": runs, **sizes}.get(pool, 1))


def manager(tmp_path, execute, parallel=2, **sizes):
    store = SchedulerStore(tmp_path / "s.db")
    return store, AgentManager(store, execute, broker(parallel, **sizes))


def agent(store, title="A", priority="normal"):
    return store.add_job(title, "do it", None, "ollama", "m", None, NEW_AGENT.to_json(), priority)


# -- the manager ---------------------------------------------------------------------------


async def test_no_more_runs_at_once_than_allowed(tmp_path):
    running, peak, release = 0, 0, asyncio.Event()

    async def execute(job, approver, control):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1
        return RunResult("c", "done")

    store, m = manager(tmp_path, execute, parallel=2)
    tasks = [asyncio.create_task(m.run(agent(store, f"J{i}"), allow)) for i in range(4)]
    await settle()
    assert running == 2
    assert sorted(r.state for r in store.list_runs()) == ["queued", "queued", "running", "running"]
    release.set()
    results = await asyncio.gather(*tasks)
    assert peak == 2 and {r.outcome for r in results} == {"done"}
    assert {r.state for r in store.list_runs()} == {"done"}


async def test_higher_priority_goes_first(tmp_path):
    order, release = [], asyncio.Event()

    async def execute(job, approver, control):
        order.append(job.title)
        await release.wait()
        return RunResult("c", "done")

    store, m = manager(tmp_path, execute, parallel=1)
    first = asyncio.create_task(m.run(agent(store, "first"), allow))
    await settle()
    waiting = []
    for title, priority in (("low", "low"), ("high", "high"), ("normal", "normal")):
        waiting.append(asyncio.create_task(m.run(agent(store, title, priority), allow)))
        await settle()
    release.set()
    await asyncio.gather(first, *waiting)
    assert order == ["first", "high", "normal", "low"]


async def test_pause_waits_for_the_checkpoint_and_resume_continues_the_conversation(tmp_path):
    steps: asyncio.Queue = asyncio.Queue()
    seen, calls = [], []

    async def execute(job, approver, control):
        calls.append(control.conversation_id)
        control.started("conv1")
        while True:
            if control.should_pause():  # the agent loop asks this between model turns
                return RunResult("conv1", "paused")
            step = await steps.get()  # one model turn
            seen.append(step)
            if step == "end":
                return RunResult("conv1", "done", "finished")

    store, m = manager(tmp_path, execute)
    task = asyncio.create_task(m.run(agent(store), allow))
    await steps.put("turn 1")
    await settle()
    [run] = store.list_runs()
    assert run.state == "running" and run.conversation_id == "conv1"
    m.pause(run.id)
    assert store.get_run(run.id).state == "running"  # the turn in progress finishes first
    await steps.put("turn 2")
    await settle()
    assert store.get_run(run.id).state == "paused" and store.get_run(run.id).reason == "user"
    with pytest.raises(ValueError, match="not running"):
        m.pause("nope")
    m.resume(run.id)
    await steps.put("end")
    result = await task
    assert seen == ["turn 1", "turn 2", "end"] and calls == [None, "conv1"]
    assert result.outcome == "done" and store.get_run(run.id).state == "done"
    with pytest.raises(ValueError, match="not paused"):
        m.resume(run.id)


async def test_resume_before_the_checkpoint_just_carries_on(tmp_path):
    steps: asyncio.Queue = asyncio.Queue()

    async def execute(job, approver, control):
        while True:
            if control.should_pause():
                return RunResult("c", "paused")
            if await steps.get() == "end":
                return RunResult("c", "done")

    store, m = manager(tmp_path, execute)
    task = asyncio.create_task(m.run(agent(store), allow))
    await settle()
    run_id = store.list_runs()[0].id
    m.pause(run_id)
    m.resume(run_id)
    await steps.put("turn")
    await steps.put("end")
    assert (await task).outcome == "done"


async def test_cancel_a_running_a_queued_and_a_paused_run(tmp_path):
    release = asyncio.Event()
    pause_now = set()

    async def execute(job, approver, control):
        if job.title in pause_now:
            return RunResult("c", "paused")
        await release.wait()
        return RunResult("c", "done")

    store, m = manager(tmp_path, execute, parallel=1)
    running = asyncio.create_task(m.run(agent(store, "running"), allow))
    await settle()
    queued = asyncio.create_task(m.run(agent(store, "queued"), allow))
    await settle()
    by_title = {store.get_job(r.job_id).title: r for r in store.list_runs()}
    m.cancel(by_title["queued"].id)
    assert (await queued).outcome == "cancelled"
    m.cancel(by_title["running"].id)
    assert (await running).outcome == "cancelled"
    assert store.get_run(by_title["running"].id).state == "cancelled"

    pause_now.add("paused")
    paused = asyncio.create_task(m.run(agent(store, "paused"), allow))
    await settle()
    run = next(r for r in store.list_runs() if store.get_job(r.job_id).title == "paused")
    m.pause(run.id)  # it pauses at once (execute returned "paused")
    await settle()
    assert store.get_run(run.id).state == "paused"
    m.cancel(run.id)
    assert (await paused).outcome == "cancelled"
    with pytest.raises(ValueError, match="already ended"):
        m.cancel(run.id)


async def test_a_job_that_is_still_running_is_skipped(tmp_path):
    release = asyncio.Event()

    async def execute(job, approver, control):
        await release.wait()
        return RunResult("c", "done")

    store, m = manager(tmp_path, execute)
    job = agent(store)
    first = asyncio.create_task(m.run(job, allow))
    await settle()
    assert (await m.run(job, allow)).outcome == "skipped"
    release.set()
    await first


async def test_held_actions_end_as_needs_you(tmp_path):
    async def execute(job, approver, control):
        with pytest.raises(ApprovalDeferred):
            await approver(ApprovalRequest("mail.send", {}, Risk.CONFIRM, "Send"))
        return RunResult("c", "done")

    async def hold(request):
        raise ApprovalDeferred("held")

    store, m = manager(tmp_path, execute)
    await m.run(agent(store), hold)
    [run] = store.list_runs()
    assert run.state == "needs-you" and run.held == 1


async def test_one_background_run_at_a_time_uses_the_browser_profile(tmp_path):
    release = asyncio.Event()

    async def execute(job, approver, control):
        await control.tool_starting("browser.open")
        await release.wait()
        return RunResult("c", "done")

    store, m = manager(tmp_path, execute)
    events = m.subscribe()
    a = asyncio.create_task(m.run(agent(store, "a"), allow))
    await settle()
    b = asyncio.create_task(m.run(agent(store, "b"), allow))
    await settle()
    states = {store.get_job(r.job_id).title: (r.state, r.reason) for r in store.list_runs()}
    assert states == {"a": ("running", None), "b": ("waiting", "browser")}
    release.set()
    await asyncio.gather(a, b)
    seen = []
    while not events.empty():
        seen.append(events.get_nowait())
    assert any(e["type"] == "tool" and e["name"] == "browser.open" for e in seen)
    assert any(e["type"] == "run" and e["run"]["state"] == "waiting" for e in seen)


async def test_a_shutdown_leaves_the_run_for_recovery_and_a_restart_continues_it(tmp_path):
    started = asyncio.Event()

    async def execute(job, approver, control):
        control.started("conv1")
        started.set()
        await asyncio.Event().wait()  # until the core stops

    store, m = manager(tmp_path, execute)
    job = agent(store)
    task = asyncio.create_task(m.run(job, allow))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    [run] = store.list_runs()
    assert run.state == "running" and run.conversation_id == "conv1"

    user_paused = store.add_run(job.id, state="paused")
    store.update_run(user_paused.id, reason="user")
    gone = store.add_run("deleted-job", state="running")

    after_restart, relaunched = AgentManager(store, execute), []
    after_restart.relaunch = lambda j, r: relaunched.append((j.id, r.id, r.conversation_id))
    after_restart.recover()
    assert relaunched == [(job.id, run.id, "conv1")]
    assert store.get_run(run.id).state == "paused" and store.get_run(run.id).reason == "restart"
    assert store.get_run(user_paused.id).state == "paused"  # waits for the user
    assert store.get_run(gone.id).state == "cancelled"
    after_restart.resume(user_paused.id)
    assert relaunched[-1][1] == user_paused.id


async def test_the_scheduler_starts_due_jobs_side_by_side(tmp_path):
    both, started = asyncio.Event(), []

    async def runner(job, approver, run=None):
        started.append(job.title)
        if len(started) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 1)  # would time out if they ran one after the other
        return RunResult("c", "done")

    async def executor(tool, args, job_id):
        return ""

    store = SchedulerStore(tmp_path / "s.db")
    now = [datetime(2026, 10, 13, 7, 0, tzinfo=UTC)]
    scheduler = Scheduler(store, runner, executor, clock=lambda: now[0])
    for title in ("one", "two"):
        scheduler.create(title, "x", {"kind": "once", "at": "2026-10-13T08:00+00:00"}, "ollama", "m")
    now[0] = datetime(2026, 10, 13, 8, 1, tzinfo=UTC)
    await scheduler.tick()
    assert sorted(started) == ["one", "two"]


# -- the loop's checkpoint -----------------------------------------------------------------


async def test_the_agent_loop_pauses_between_model_turns():
    turns = [
        [TurnDone(tool_calls=[ToolCall(id="c1", name="current_time", arguments={})])],
        [TextDelta("never"), TurnDone(text="never")],
    ]

    async def turn_fn(provider, model, messages, tools, system, max_tokens):
        for event in turns.pop(0):
            yield event

    provider = Provider(id="t", name="T", api_style=ApiStyle.OPENAI, base_url="http://x", requires_key=False)
    asked = []

    def should_pause():
        asked.append(1)
        return len(asked) > 1  # go for one turn, then pause

    agent = Agent(provider, "m", ToolRegistry(core_tools()), SkillRegistry(), turn_fn=turn_fn,
                  should_pause=should_pause)
    messages = [Message(role="user", content="time?")]
    events = [e async for e in agent.run(messages)]
    assert isinstance(events[-1], Finished) and events[-1].reason == "paused"
    assert [m.role for m in messages] == ["user", "assistant", "tool"]  # the tool call has its result
    assert len(turns) == 1  # the second model turn never happened


# -- scope ---------------------------------------------------------------------------------


def test_scope_parsing():
    assert parse_scope(None) == NEW_AGENT and NEW_AGENT.browser == "fresh" and NEW_AGENT.folders == ()
    assert parse_scope({"skills": ["a", "a", " "], "accounts": ["B@X.com"]}).accounts == ("b@x.com",)
    assert parse_scope({"skills": ["a", "a", " "]}).skills == ("a",)
    for bad, message in (
        ({"browser": "chrome"}, "browser"),
        ({"skills": "a"}, "list"),
        ({"folders": ["relative/path"]}, "full folder path"),
        ({"powers": []}, "Unknown"),
        ([], "object"),
    ):
        with pytest.raises(ScopeError, match=message):
            parse_scope(bad)
    assert "your signed-in browser" in LEGACY.describe() and "all approved" in LEGACY.describe()
    assert "its own;" in NEW_AGENT.describe() and "a clean browser" in NEW_AGENT.describe()


async def test_an_agent_uses_only_its_own_folder_and_the_folders_it_was_given(tmp_path):
    shared, other = tmp_path / "shared", tmp_path / "other"
    shared.mkdir()
    other.mkdir()
    workspace = tmp_path / "ws"
    folders = ApprovedFolders(tmp_path / "f.json", workspace=workspace)
    folders.set([FolderGrant(shared), FolderGrant(other)])
    tools = {t.name: t for t in file_tools(folders)}
    home = workspace / "agents" / "mine-abc123"
    ctx = RunContext(skills=None)

    with use_scope(Scope(folders=(str(shared),), home=home)):
        await tools["files.write"].handler({"path": "notes.txt", "content": "hi"}, ctx)
        assert (home / "notes.txt").read_text() == "hi"  # relative = its own folder, made on first use
        assert tools["files.write"].risk_of({"path": "more.txt", "content": "x"}) is Risk.AUTO
        assert tools["files.write"].risk_of({"path": "notes.txt", "overwrite": True}) is Risk.CONFIRM
        await tools["files.list"].handler({"path": str(shared)}, ctx)
        for outside in (other, workspace / "x.txt", workspace / "agents" / "theirs-def456" / "x.txt"):
            with pytest.raises(FileAccessError, match="outside"):
                await tools["files.list"].handler({"path": str(outside)}, ctx)
        listing = await tools["files.folders"].handler({}, ctx)
        assert str(home) in listing and str(other) not in listing

    with use_scope(Scope(home=home)):  # made before scopes: every approved folder, still not the workspace
        await tools["files.list"].handler({"path": str(other)}, ctx)
        with pytest.raises(FileAccessError, match="outside"):
            await tools["files.list"].handler({"path": str(workspace)}, ctx)

    # The chat is unchanged.
    await tools["files.write"].handler({"path": "chat.txt", "content": "x"}, ctx)
    assert (workspace / "chat.txt").exists()


def test_an_agent_uses_only_its_google_accounts(monkeypatch):
    auth = GoogleAuth()
    monkeypatch.setattr(auth, "accounts", lambda: ["a@x.com", "b@x.com"])
    with use_scope(Scope(accounts=("b@x.com",))):
        assert auth.resolve(None) == "b@x.com"  # the only one it may use, so no name is needed
        with pytest.raises(GoogleError, match="may not use 'A@x.com'"):
            auth.resolve("A@x.com")
    with use_scope(Scope(accounts=())):
        with pytest.raises(GoogleError, match="any Google account"):
            auth.resolve(None)
        with pytest.raises(GoogleError, match="It may use: no Google account"):
            auth.resolve("b@x.com")
    with pytest.raises(GoogleError, match="Several"):
        auth.resolve(None)  # the chat: unchanged


async def test_the_browser_follows_the_agents_browser_mode():
    tools = {t.name: t for t in browser_tools(None)}
    ctx = RunContext(skills=None)
    with use_scope(Scope(browser="off")), pytest.raises(BrowserError, match="may not use the browser"):
        await tools["browser.open"].handler({"url": "https://example.com"}, ctx)
    with use_scope(Scope(browser="fresh")), pytest.raises(BrowserError, match="clean browser"):
        await tools["browser.read"].handler({}, ctx)


SKILL = """---
name: {name}
description: Skill {name}.
---
Do {name}.
"""


async def test_an_agent_sees_and_loads_only_its_skills(tmp_path):
    for name in ("alpha", "beta"):
        (tmp_path / "b" / name).mkdir(parents=True)
        (tmp_path / "b" / name / "SKILL.md").write_text(SKILL.format(name=name), encoding="utf-8")
    skills = SkillRegistry(builtin_dir=tmp_path / "b", user_dir=tmp_path / "u")
    view = skills.restricted(["alpha"])
    assert view.get("beta") is None and skills.get("beta") is not None
    assert "alpha" in view.catalog_prompt() and "beta" not in view.catalog_prompt()
    assert skills.restricted(None) is skills
    load = ToolRegistry(core_tools()).get("load_skill")
    assert "No skill named 'beta'" in await load.handler({"name": "beta"}, RunContext(skills=view))


# -- tools ---------------------------------------------------------------------------------


async def test_agent_create_needs_approval_and_shows_the_scope(tmp_path):
    ran = asyncio.Event()

    async def execute(job, approver, control):
        ran.set()
        return RunResult("c", "done")

    async def executor(tool, args, job_id):
        return ""

    store, m = manager(tmp_path, execute)
    scheduler = Scheduler(store, m.run, executor)
    approved = tmp_path / "docs"
    approved.mkdir()
    create, listing, cancel = agent_tools(scheduler, m, lambda: [approved])
    assert create.risk is Risk.CONFIRM and cancel.risk is Risk.CONFIRM and listing.risk is Risk.AUTO
    summary = create.summary_of({"title": "Prices", "prompt": "watch prices", "folders": [str(approved)]})
    assert "starts now, once" in summary and str(approved) in summary and "a clean browser" in summary
    ctx = RunContext(skills=None, provider_id="ollama", model="m")
    with pytest.raises(ValueError, match="not an approved folder"):
        await create.handler({"title": "x", "prompt": "y", "folders": [str(tmp_path)]}, ctx)
    out = await create.handler({"title": "Prices", "prompt": "watch prices", "browser": "off"}, ctx)
    assert "Started the agent" in out
    await asyncio.wait_for(ran.wait(), 1)
    await settle()  # the run ends
    [job] = store.list_jobs()
    assert job.rule is None and job.scope["browser"] == "off" and job.scope["folders"] == []
    out = await create.handler(
        {"title": "Daily", "prompt": "p", "when": {"kind": "daily", "time": "08:00"}, "priority": "high"}, ctx
    )
    assert "Every day at 08:00" in out
    assert "Prices: When started" in await listing.handler({}, ctx)
    assert "is not running" in await cancel.handler({"id": job.id}, ctx)


def test_old_scheduler_databases_get_the_agent_columns(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL,"
            " rule TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, enabled INTEGER NOT NULL,"
            " next_run TEXT, last_run TEXT, last_status TEXT, last_conversation_id TEXT,"
            " created_at TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO jobs VALUES ('j1', 'Brief', 'p', ?, 'ollama', 'm', 1, NULL, NULL, NULL, NULL,"
            " '2026-10-01T00:00:00+00:00')",
            ('{"kind": "daily", "time": "08:00"}',),
        )
    [job] = SchedulerStore(path).list_jobs()
    assert job.scope is None and job.priority == "normal" and job.folder is None
    assert job.rule["kind"] == "daily"


# -- API -----------------------------------------------------------------------------------


def make_client(tmp_path, turn_fn):
    skills = SkillRegistry(builtin_dir=tmp_path / "a", user_dir=tmp_path / "b")
    app = server.create_app(
        TOKEN, skills=skills, turn_fn=turn_fn, scheduler_store=SchedulerStore(tmp_path / "api.db"),
        start_scheduler=False,
    )
    return TestClient(app)


def wait_for(client, run_id, states=("done", "needs-you", "failed", "cancelled")):
    for _ in range(200):
        run = next(r for r in client.get("/api/agent-runs", headers=AUTH).json() if r["id"] == run_id)
        if run["state"] in states:
            return run
        time.sleep(0.02)
    raise AssertionError(f"run stuck in {run['state']}")


def test_api_agent_lifecycle_and_a_run(tmp_path):
    client = make_client(tmp_path, scripted([TextDelta("Done."), TurnDone(text="Done.")]))
    body = {"title": "Research", "prompt": "Look into it", "provider": "ollama", "model": "m"}
    bad = client.post("/api/agents", json={**body, "scope": {"folders": [str(tmp_path)]}}, headers=AUTH)
    assert bad.status_code == 400 and "not an approved folder" in bad.json()["detail"]
    assert client.post("/api/agents", json={**body, "priority": "urgent"}, headers=AUTH).status_code == 400
    made = client.post("/api/agents", json=body, headers=AUTH)
    assert made.status_code == 201
    agent = made.json()
    assert agent["rule"] is None and agent["when"] == "When started" and agent["scope"]["browser"] == "fresh"
    assert not agent["legacy"] and agent["folder"].endswith(f"research-{agent['id'][:6]}")
    assert client.get("/api/scheduler/jobs", headers=AUTH).json() == []  # not a scheduled job

    edited = client.put(
        f"/api/agents/{agent['id']}", json={"scope": {"browser": "off"}, "priority": "high"}, headers=AUTH
    ).json()
    assert edited["scope"]["browser"] == "off" and edited["priority"] == "high"

    with client:  # one event loop, so the background run can finish
        started = client.post(f"/api/agents/{agent['id']}/run", headers=AUTH)
        assert started.status_code == 202
        run = wait_for(client, started.json()["id"])
        assert run["state"] == "done" and run["conversation_id"]
        conv = client.get(f"/api/conversations/{run['conversation_id']}", headers=AUTH).json()
        assert conv["title"] == "Agent: Research"
        events = client.get("/api/scheduler/events", headers=AUTH).json()
        assert events[0]["kind"] == "finished" and events[0]["body"] == "Done."
        ended = client.post(f"/api/agent-runs/{run['id']}/cancel", headers=AUTH)
        assert ended.status_code == 400
        assert client.post(f"/api/agent-runs/{run['id']}/explode", headers=AUTH).status_code == 404

    assert client.delete(f"/api/agents/{agent['id']}", headers=AUTH).status_code == 204
    assert client.get("/api/agents", headers=AUTH).json() == []


def test_api_scheduled_jobs_are_agents_with_the_old_rights(tmp_path):
    client = make_client(tmp_path, scripted())
    body = {"title": "Brief", "prompt": "p", "rule": {"kind": "daily", "time": "08:00"}, "provider": "ollama",
            "model": "m"}
    jid = client.post("/api/scheduler/jobs", json=body, headers=AUTH).json()["id"]
    [agent] = client.get("/api/agents", headers=AUTH).json()
    assert agent["id"] == jid and agent["legacy"] and agent["scope"]["browser"] == "profile"
    assert agent["scope"]["folders"] is None and "all approved" in agent["scope_text"]


def test_events_socket_checks_the_token(tmp_path):
    client = make_client(tmp_path, scripted())
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/events?token=wrong") as ws:
        ws.receive_json()
    with client.websocket_connect(f"/ws/events?token={TOKEN}"):
        pass
