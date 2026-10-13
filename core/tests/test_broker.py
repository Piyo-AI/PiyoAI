import asyncio
import json
import random
import threading
import time

import pytest
from fastapi.testclient import TestClient
from test_agents import agent, allow, manager, settle, wait_for
from test_runs import AUTH, TOKEN, chat

from piyo.agent import Agent
from piyo.agents import Holder, ResourceBroker, leased_turn, use_holder
from piyo.agents.broker import AGING_S, pool_capacity
from piyo.config import data_dir
from piyo.config.resource_limits import PRESETS, Limits, ResourceLimits, preset_of
from piyo.models import Provider
from piyo.models.providers import ApiStyle
from piyo.models.turn import Message, TextDelta, ToolCall, TurnDone
from piyo.scheduler import RunResult, SchedulerStore
from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.tools import RunContext, ToolRegistry, core_tools
from piyo.tools.scripts import script_tools

PROVIDER = Provider(id="cloud", name="Cloud", api_style=ApiStyle.OPENAI, base_url="https://x.test")


def sized(**sizes):
    return ResourceBroker(lambda pool: sizes.get(pool, 1))


def bg(name, priority="normal", **kw):
    return Holder(name, priority, **kw)


async def queue_up(broker, pool, holders):
    """Start a wait for each holder in order; returns the tasks."""
    tasks = []
    for h in holders:
        tasks.append(asyncio.create_task(broker.acquire(pool, h)))
        await settle()
    return tasks


# -- the broker ----------------------------------------------------------------------------


async def test_a_pool_is_never_over_its_size_under_many_concurrent_runs():
    broker, inside, peak = sized(runs=3), 0, 0
    rng = random.Random(7)

    async def work(i):
        nonlocal inside, peak
        holder = bg(f"r{i}", rng.choice(["low", "normal", "high"]))
        async with broker.lease("runs", holder):
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(rng.random() / 200)
            inside -= 1

    await asyncio.gather(*(work(i) for i in range(40)))
    assert peak == 3 and inside == 0
    assert broker.usage()["runs"] == {"capacity": 3, "used": 0, "waiting": 0}


async def test_waiting_runs_go_by_priority_then_oldest_first():
    broker = sized(runs=1)
    first = await broker.acquire("runs", bg("first"))
    holders = [bg("low", "low"), bg("normal-1"), bg("high", "high"), bg("normal-2")]
    tasks = await queue_up(broker, "runs", holders)
    order = []
    lease = first
    for _ in holders:
        broker.release(lease)
        await settle()
        done = next(t for t in tasks if t.done() and t not in order)
        order.append(done)
        lease = done.result()
    assert [t.result().holder.id for t in order] == ["high", "normal-1", "normal-2", "low"]


async def test_a_long_wait_raises_a_low_priority_request_so_it_is_not_starved():
    now = [0.0]
    broker = ResourceBroker(lambda pool: 1, clock=lambda: now[0])
    lease = await broker.acquire("runs", bg("busy"))
    old_low = asyncio.create_task(broker.acquire("runs", bg("old-low", "low")))
    await settle()
    now[0] = 2 * AGING_S + 1  # two levels up: above a fresh normal one
    fresh = asyncio.create_task(broker.acquire("runs", bg("fresh-normal")))
    await settle()
    broker.release(lease)
    await settle()
    assert old_low.done() and not fresh.done()
    broker.release(old_low.result())
    await settle()
    assert fresh.done()


async def test_the_chat_never_waits_behind_background_model_calls():
    broker = sized(**{"model:cloud": 2})
    a = await broker.acquire("model:cloud", bg("a"))
    b = asyncio.create_task(broker.acquire("model:cloud", bg("b", "high")))
    await settle()
    assert not b.done()  # the second slot is kept for the chat
    chat_lease = await asyncio.wait_for(broker.acquire("model:cloud", Holder("chat", interactive=True)), 1)
    assert broker.usage()["model:cloud"]["used"] == 2
    broker.release(chat_lease)
    await settle()
    assert not b.done()  # still kept for the chat
    broker.release(a)
    await settle()
    assert b.done()


async def test_with_room_for_one_the_chat_goes_next_but_does_not_cut_in():
    broker = sized(**{"model:local": 1})
    busy = await broker.acquire("model:local", bg("busy"))
    later = asyncio.create_task(broker.acquire("model:local", bg("earlier", "high")))
    await settle()
    chat_wait = asyncio.create_task(broker.acquire("model:local", Holder("chat", interactive=True)))
    await settle()
    assert not chat_wait.done()  # nothing is cut off mid-call
    broker.release(busy)
    await settle()
    assert chat_wait.done() and not later.done()
    broker.release(chat_wait.result())
    await settle()
    assert later.done()


async def test_cancelling_a_wait_leaves_nothing_behind():
    broker = sized(runs=1)
    lease = await broker.acquire("runs", bg("a"))
    waiting = asyncio.create_task(broker.acquire("runs", bg("b")))
    await settle()
    waiting.cancel()
    await asyncio.gather(waiting, return_exceptions=True)
    assert broker.usage()["runs"]["waiting"] == 0
    broker.release(lease)
    assert broker.usage()["runs"]["used"] == 0

    # Granted just as it was cancelled: the lease is passed on, not lost.
    lease = await broker.acquire("runs", bg("a"))
    granted_late = asyncio.create_task(broker.acquire("runs", bg("b")))
    next_one = asyncio.create_task(broker.acquire("runs", bg("c")))
    await settle()
    broker.release(lease)  # hands it to b
    granted_late.cancel()  # before b ever ran again
    await asyncio.gather(granted_late, return_exceptions=True)
    await settle()
    assert next_one.done() and broker.usage()["runs"]["used"] == 1


async def test_the_sweep_returns_everything_and_a_late_release_is_harmless():
    broker = sized(runs=2, scripts=2)
    holder = bg("a")
    run = await broker.acquire("runs", holder)
    await broker.acquire("scripts", holder)
    assert sorted(broker.held(holder)) == ["runs", "scripts"]
    broker.release_all(holder)
    assert broker.held(holder) == [] and all(u["used"] == 0 for u in broker.usage().values())
    broker.release(run)  # the owner's own release coming after the sweep
    assert broker.usage()["runs"]["used"] == 0


async def test_a_higher_priority_run_asks_the_lowest_holder_to_make_room():
    broker, asked = sized(runs=2), []

    def holder(name, priority):
        return bg(name, priority, preempt=lambda: asked.append(name))

    await broker.acquire("runs", holder("normal", "normal"))
    low_lease = await broker.acquire("runs", holder("low", "low"))
    high = asyncio.create_task(broker.acquire("runs", holder("high", "high")))
    await settle()
    assert asked == ["low"]  # the lowest, not the normal one
    also_high = asyncio.create_task(broker.acquire("runs", holder("high-2", "high")))
    await settle()
    assert asked == ["low"]  # one is already making room
    broker.release(low_lease)  # it reached its checkpoint
    await settle()
    assert high.done() and not also_high.done()
    also_high.cancel()
    await asyncio.gather(also_high, return_exceptions=True)

    asked.clear()
    equals = sized(runs=1)
    await equals.acquire("runs", holder("normal", "normal"))
    same = asyncio.create_task(equals.acquire("runs", holder("normal-2", "normal")))
    await settle()
    assert asked == []  # an equal one waits its turn
    same.cancel()
    await asyncio.gather(same, return_exceptions=True)


async def test_the_chat_is_never_asked_to_make_room():
    broker, asked = sized(browser=1), []
    await broker.acquire("browser", Holder("chat", interactive=True, preempt=lambda: asked.append("chat")))
    waiting = asyncio.create_task(broker.acquire("browser", bg("high", "high")))
    await settle()
    assert asked == []
    waiting.cancel()
    await asyncio.gather(waiting, return_exceptions=True)


async def test_raising_a_limit_lets_waiting_requests_go():
    sizes = {"runs": 1}
    broker = ResourceBroker(lambda pool: sizes[pool])
    await broker.acquire("runs", bg("a"))
    waiting = asyncio.create_task(broker.acquire("runs", bg("b")))
    await settle()
    sizes["runs"] = 2
    broker.limits_changed()
    await settle()
    assert waiting.done()


async def test_wait_time_is_recorded_and_the_holder_is_told():
    broker, seen = sized(scripts=1), []
    lease = await broker.acquire("scripts", bg("a"))
    holder = bg("b", on_wait=lambda p: seen.append(("wait", p)), on_granted=lambda p: seen.append(("go", p)))
    waiting = asyncio.create_task(broker.acquire("scripts", holder))
    await asyncio.sleep(0.05)
    broker.release(lease)
    await waiting
    assert seen == [("wait", "scripts"), ("go", "scripts")] and holder.waited >= 0.04


async def test_model_calls_hold_a_lease_while_they_stream():
    broker, seen = sized(**{"model:cloud": 3}), []

    async def turn(provider, model, messages, specs, system, max_tokens):
        seen.append(broker.usage()["model:cloud"]["used"])
        yield TextDelta("hi")
        yield TurnDone(text="hi")

    holder = bg("a")
    with use_holder(holder):
        events = [e async for e in leased_turn(turn, broker)(PROVIDER, "m", [], [], "", 10)]
    assert seen == [1] and events[-1].text == "hi"
    assert broker.usage()["model:cloud"]["used"] == 0 and holder.waited == 0


async def test_waiting_for_a_model_slot_does_not_count_toward_the_timeout(tmp_path):
    broker = sized(**{"model:cloud": 1})
    call = ToolCall(id="1", name="current_time", arguments={})
    turns = iter([TurnDone(tool_calls=[call]), TurnDone(text="ok")])

    async def turn(provider, model, messages, specs, system, max_tokens):
        yield next(turns)

    holder = bg("a")
    skills = SkillRegistry(builtin_dir=tmp_path, user_dir=tmp_path)
    agent_ = Agent(
        PROVIDER, "m", ToolRegistry(core_tools()), skills, turn_fn=leased_turn(turn, broker), timeout_s=0.1,
        waited=lambda: holder.waited,
    )
    other = await broker.acquire("model:cloud", bg("other"))

    async def let_go_later():
        await asyncio.sleep(0.3)
        broker.release(other)

    asyncio.create_task(let_go_later())
    with use_holder(holder):
        events = [e async for e in agent_.run([Message(role="user", content="time?")])]
    assert holder.waited >= 0.25 and events[-1].reason == "done"


async def test_scripts_wait_for_a_free_script_slot_after_the_gate():
    broker, inside, peak = sized(scripts=1), 0, 0

    class Result:
        value = {"ok": True}

    class Runner:
        async def run(self, skill, script, extra):
            nonlocal inside, peak
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0.01)
            inside -= 1
            return Result()

        def download_note(self, skill, script):
            return ""

    class Skills:
        def get(self, name):
            return type("S", (), {"source": "builtin"})()

    [tool] = script_tools(Runner(), Skills(), broker)
    ctx = RunContext(skills=Skills(), active_skills={"s"})
    outs = await asyncio.gather(*(tool.handler({"skill": "s", "script": "x.py"}, ctx) for _ in range(3)))
    assert peak == 1 and all('"ok": true' in o for o in outs)


# -- the agent manager on the broker -------------------------------------------------------


async def test_a_preempted_run_pauses_at_its_checkpoint_and_queues_again(tmp_path):
    steps, order = asyncio.Queue(), []

    async def execute(job, approver, control):
        order.append(job.title)
        if job.title == "high":
            return RunResult("c", "done")
        while True:
            if control.should_pause():
                return RunResult("c", "paused")
            if await steps.get() == "end":
                return RunResult("c", "done")

    store, m = manager(tmp_path, execute, parallel=1)
    low = asyncio.create_task(m.run(agent(store, "low", "low"), allow))
    await settle()
    high = asyncio.create_task(m.run(agent(store, "high", "high"), allow))
    await settle()
    runs = {store.get_job(r.job_id).title: r for r in store.list_runs()}
    assert runs["low"].state == "running" and runs["high"].state == "queued"
    await steps.put("a turn")  # the low run reaches its checkpoint and steps aside
    await high
    await settle()
    assert store.get_run(runs["low"].id).state == "running"
    await steps.put("end")
    await low
    assert order == ["low", "high", "low"]
    assert store.get_run(runs["low"].id).state == "done"
    assert m.broker.usage()["runs"]["used"] == 0


async def test_leases_come_back_when_a_run_is_cancelled_or_crashes(tmp_path):
    started = asyncio.Event()

    async def execute(job, approver, control):
        await control.manager.broker.acquire("scripts", control.holder)  # never given back by itself
        await control.tool_starting("browser.open")
        if job.title == "crash":
            raise RuntimeError("boom")
        started.set()
        await asyncio.Event().wait()

    store, m = manager(tmp_path, execute, parallel=2, scripts=2)
    crashed = await m.run(agent(store, "crash"), allow)
    assert crashed.outcome == "error" and store.list_runs()[0].state == "failed"
    task = asyncio.create_task(m.run(agent(store, "hang"), allow))
    await started.wait()
    assert {p: u["used"] for p, u in m.broker.usage().items()} == {"browser": 1, "runs": 1, "scripts": 1}
    m.cancel(next(r.id for r in store.list_runs() if r.state == "running"))
    assert (await task).outcome == "cancelled"
    assert all(u["used"] == 0 for u in m.broker.usage().values())


async def test_a_run_waiting_for_a_model_slot_shows_it(tmp_path):
    release = asyncio.Event()

    async def execute(job, approver, control):
        async with control.manager.broker.lease("model:cloud", control.holder):
            await release.wait()
        return RunResult("c", "done")

    store, m = manager(tmp_path, execute, parallel=2)
    a = asyncio.create_task(m.run(agent(store, "a"), allow))
    await settle()
    b = asyncio.create_task(m.run(agent(store, "b"), allow))
    await settle()
    states = {store.get_job(r.job_id).title: (r.state, r.reason) for r in store.list_runs()}
    assert states == {"a": ("running", None), "b": ("waiting", "model:cloud")}
    release.set()
    await asyncio.gather(a, b)


# -- limits --------------------------------------------------------------------------------


def test_presets_and_custom_limits():
    limits = ResourceLimits()
    assert limits.get() == PRESETS["balanced"] and preset_of(limits.get()) == "balanced"
    assert limits.use_preset("light").runs == 2
    custom = Limits(**{**vars(PRESETS["light"]), "runs": 3})
    assert preset_of(limits.set(custom)) == "custom"
    assert ResourceLimits().get() == custom  # saved
    with pytest.raises(ValueError, match="between 1 and 16"):
        limits.set(Limits(**{**vars(custom), "runs": 0}))
    with pytest.raises(ValueError, match="Tabs per run"):
        limits.set(Limits(**{**vars(custom), "tabs_per_run": 9, "browser_tabs": 6}))
    with pytest.raises(ValueError, match="Unknown preset"):
        limits.use_preset("turbo")
    (data_dir() / ResourceLimits.filename).write_text(json.dumps({"runs": "lots"}), encoding="utf-8")
    assert ResourceLimits().get() == PRESETS["balanced"]  # a damaged file falls back to the default


def test_pool_sizes_follow_the_limits():
    light = PRESETS["light"]
    assert pool_capacity(light, "runs") == 2 and pool_capacity(light, "scripts") == 1
    assert pool_capacity(light, "model:openrouter") == 2
    assert pool_capacity(PRESETS["heavy"], "model:ollama", local=True) == 1
    assert pool_capacity(light, "browser") == 1


def test_api_resource_limits():
    client = TestClient(server.create_app(TOKEN, start_scheduler=False))
    got = client.get("/api/resource-limits", headers=AUTH).json()
    assert got["preset"] == "balanced" and set(got["presets"]) == {"light", "balanced", "heavy"}
    assert got["bounds"]["runs"] == [1, 16]
    light = client.put("/api/resource-limits", json={"preset": "light"}, headers=AUTH).json()
    assert light["preset"] == "light" and light["limits"]["runs"] == 2
    custom = {**light["limits"], "scripts": 3}
    out = client.put("/api/resource-limits", json={"limits": custom}, headers=AUTH).json()
    assert out["preset"] == "custom" and out["limits"]["scripts"] == 3
    bad = client.put("/api/resource-limits", json={"limits": {**custom, "runs": 99}}, headers=AUTH)
    assert bad.status_code == 400 and "runs" in bad.json()["detail"]
    assert client.put("/api/resource-limits", json={}, headers=AUTH).status_code == 400
    assert client.get("/api/resource-limits").status_code == 401


def test_the_chat_answers_while_background_agents_use_the_model(tmp_path):
    go = threading.Event()

    async def turn_fn(provider, model, messages, tools, system, max_tokens):
        if messages[0].content == "slow":
            while not go.is_set():
                await asyncio.sleep(0.01)
        yield TurnDone(text="ok")

    skills = SkillRegistry(builtin_dir=tmp_path / "a", user_dir=tmp_path / "b")
    app = server.create_app(
        TOKEN, skills=skills, turn_fn=turn_fn, scheduler_store=SchedulerStore(tmp_path / "s.db"),
        start_scheduler=False,
    )
    client = TestClient(app)
    limits = {**client.get("/api/resource-limits", headers=AUTH).json()["limits"], "local_model_calls": 2}
    client.put("/api/resource-limits", json={"limits": limits}, headers=AUTH)
    body = {"prompt": "slow", "provider": "ollama", "model": "m"}
    ids = [client.post("/api/agents", json={**body, "title": t}, headers=AUTH).json()["id"] for t in "ab"]
    with client:
        runs = [client.post(f"/api/agents/{i}/run", headers=AUTH).json()["id"] for i in ids]
        for _ in range(200):
            if app.state.agents.broker.usage().get("model:ollama", {}).get("waiting") == 1:
                break
            time.sleep(0.02)
        waiting = [r for r in client.get("/api/agent-runs", headers=AUTH).json() if r["reason"]]
        assert [(r["state"], r["reason"]) for r in waiting] == [("waiting", "model:ollama")]
        started = time.monotonic()
        assert chat(client, "hi")["reason"] == "done"  # the kept slot
        assert time.monotonic() - started < 2 and not go.is_set()
        go.set()
        assert all(wait_for(client, r)["state"] == "done" for r in runs)
