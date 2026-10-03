import time

from fastapi.testclient import TestClient
from test_agent import PROVIDER, Script, call
from test_server import AUTH, TOKEN, chat_request, scripted

from piyo.agent import Agent, Finished, ToolFinished
from piyo.models import parse_openrouter_models
from piyo.models.capabilities import ModelCaps, model_issues
from piyo.models.turn import Message, ToolCall, TurnDone
from piyo.safety import PermissionGate
from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.skills.manifest import ModelNeeds
from piyo.tools import Risk, Tool, ToolRegistry, core_tools

SEER = """---
name: seer
description: Reads screenshots.
requires:
  model:
    vision: true
    min_context: 16000
---
Look at the screen.
"""


def make_skills(tmp_path, text=SEER, name="seer"):
    d = tmp_path / "u" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "u")


def test_known_mismatches_are_reported_unknown_passes():
    needs = ModelNeeds(vision=True, min_context=16000)
    assert model_issues(needs, ModelCaps("m", 32000, vision=True)) == []
    assert model_issues(needs, ModelCaps("m", 32000, vision=None)) == []  # unknown never blocks
    issues = model_issues(needs, ModelCaps("m", 8000, vision=False))
    assert len(issues) == 2 and "images" in issues[0] and "16000" in issues[1]
    assert model_issues(ModelNeeds(), ModelCaps("m", 1, vision=False)) == []


def test_openrouter_vision_comes_from_input_modalities():
    payload = {
        "data": [
            {"id": "a", "architecture": {"input_modalities": ["text", "image"]}},
            {"id": "b", "architecture": {"input_modalities": ["text"]}},
            {"id": "c"},
        ]
    }
    assert [m.vision for m in parse_openrouter_models(payload)] == [True, False, None]


async def test_load_skill_refused_with_clear_message(tmp_path):
    skills = make_skills(tmp_path)
    script = Script(
        [TurnDone(tool_calls=[call("load_skill", name="seer")])],
        [TurnDone(text="That skill needs a vision model.")],
    )
    agent = Agent(
        PROVIDER, "m", ToolRegistry(core_tools()), skills, turn_fn=script,
        model_caps=ModelCaps("m", 32000, vision=False),
    )
    messages = [Message(role="user", content="look")]
    events = [e async for e in agent.run(messages)]
    result = next(e for e in events if isinstance(e, ToolFinished))
    assert "can't run with the current model" in result.output and "can't" in result.output
    assert agent.active_skills == set()  # nothing was loaded
    assert "NOT USABLE" in script.seen[0]["system"]  # and the catalog said so up front


def test_catalog_marks_only_unusable_skills(tmp_path):
    skills = make_skills(tmp_path)
    assert "NOT USABLE" not in skills.catalog_prompt()
    assert "NOT USABLE" not in skills.catalog_prompt(ModelCaps("m", 32000, vision=True))
    assert "NOT USABLE" in skills.catalog_prompt(ModelCaps("m", 32000, vision=False))


def test_skills_api_lists_issues_for_the_chosen_model(tmp_path):
    client = TestClient(server.create_app(TOKEN, skills=make_skills(tmp_path)))
    plain = client.get("/api/skills", headers=AUTH).json()["skills"][0]
    assert plain["model_issues"] == []
    # No vision report for this model, and the default 32k window covers 16k: nothing known to be wrong.
    ok = client.get("/api/skills?provider=ollama&model=m", headers=AUTH).json()["skills"][0]
    assert ok["model_issues"] == []
    client.put(
        "/api/context-limit", json={"provider": "ollama", "model": "m", "tokens": 8192}, headers=AUTH
    )
    small = client.get("/api/skills?provider=ollama&model=m", headers=AUTH).json()["skills"][0]
    assert "16000" in small["model_issues"][0]


def looping_agent(**kw):
    """An agent whose model asks for a tool call forever."""

    async def noop(args, ctx):
        return "ok"

    async def forever(provider, model, messages, tools, system, max_tokens):
        yield TurnDone(
            tool_calls=[ToolCall(id="c", name="noop", arguments={})],
            input_tokens=600,
            output_tokens=400,
        )

    tools = ToolRegistry(core_tools() + [Tool("noop", "n", noop, core=True)])
    return Agent(PROVIDER, "m", tools, SkillRegistry(builtin_dir=None, user_dir=None), turn_fn=forever, **kw)


async def run(agent):
    return [e async for e in agent.run([Message(role="user", content="go")])]


async def test_token_budget_stops_the_run_after_a_complete_turn():
    agent = looping_agent(max_total_tokens=2500)
    events = await run(agent)
    assert events[-1] == Finished("token_limit", [])
    assert agent.tokens_used == 3000  # stops at the next turn boundary, not mid tool call
    assert sum(isinstance(e, ToolFinished) for e in events) == 3  # every call got its result


async def test_timeout_stops_the_run():
    agent = looping_agent(timeout_s=0.05)
    time.sleep(0)  # the loop itself is fast; make the model slow instead
    original = agent._turn

    async def slow(*a):
        time.sleep(0.06)
        async for e in original(*a):
            yield e

    agent._turn = slow
    assert (await run(agent))[-1].reason == "timeout"


async def test_time_waiting_for_approval_is_not_counted():
    async def slow_yes(req):
        time.sleep(0.2)
        return True

    async def noop(args, ctx):
        return "ok"

    script = Script(
        [TurnDone(tool_calls=[call("ask")])],
        [TurnDone(text="done")],
    )
    tools = ToolRegistry([Tool("ask", "a", noop, risk=Risk.CONFIRM, core=True)])
    agent = Agent(
        PROVIDER, "m", tools, SkillRegistry(builtin_dir=None, user_dir=None),
        PermissionGate(slow_yes), turn_fn=script, timeout_s=0.1,
    )
    assert (await run(agent))[-1].reason == "done"


def test_run_settings_defaults_validation_and_persistence():
    client = TestClient(server.create_app(TOKEN))
    got = client.get("/api/run-settings", headers=AUTH).json()
    assert got == {"max_steps": 20, "max_tokens": 200000, "timeout_s": 600, "local_only": False}
    bad = client.put("/api/run-settings", json={"max_steps": 0}, headers=AUTH)
    assert bad.status_code == 400
    put = client.put("/api/run-settings", json={"timeout_s": 60, "local_only": True}, headers=AUTH)
    assert put.json()["timeout_s"] == 60 and put.json()["max_steps"] == 20  # others untouched
    assert client.get("/api/run-settings", headers=AUTH).json()["local_only"] is True


def test_local_only_refuses_cloud_providers_but_allows_local():
    turn_fn = scripted([TurnDone(text="hi")])
    client = TestClient(server.create_app(TOKEN, turn_fn=turn_fn))
    client.put("/api/run-settings", json={"local_only": True}, headers=AUTH)
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json(chat_request(provider="anthropic", model="m"))
        err = ws.receive_json()
        assert err["type"] == "error" and "Local-only" in err["message"]
        ws.send_json(chat_request())  # ollama is local
        assert ws.receive_json()["type"] == "conversation"
        assert ws.receive_json() == {"type": "done", "reason": "done"}


def test_vision_override_beats_the_provider_and_unknown_stays_unknown(tmp_path):
    from piyo.config.model_vision import ModelVision

    v = ModelVision(tmp_path / "v.json")
    assert v.resolve("p", "m") is None and v.resolve("p", "m", True) is True
    v.set("p", "m", "no")
    assert v.resolve("p", "m", True) is False
    v.set("p", "m", "yes")
    assert v.resolve("p", "m", False) is True
    v.set("p", "m", "auto")
    assert v.resolve("p", "m", False) is False and v.get("p", "m") == "auto"
    import pytest

    with pytest.raises(ValueError):
        v.set("p", "m", "maybe")


def test_vision_api_changes_what_skills_report(tmp_path):
    client = TestClient(server.create_app(TOKEN, skills=make_skills(tmp_path)))
    q = {"provider": "ollama", "model": "m"}

    def issues():
        return client.get("/api/skills?provider=ollama&model=m", headers=AUTH).json()["skills"][0][
            "model_issues"
        ]

    assert client.get("/api/vision", params=q, headers=AUTH).json() == {
        "mode": "auto", "effective": None, "reported": None,
    }
    assert issues() == []  # unknown never blocks
    put = client.put("/api/vision", json={**q, "mode": "no"}, headers=AUTH).json()
    assert put["effective"] is False and "images" in issues()[0]
    client.put("/api/vision", json={**q, "mode": "yes"}, headers=AUTH)
    assert issues() == []
    assert client.put("/api/vision", json={**q, "mode": "x"}, headers=AUTH).status_code == 400


def test_model_capabilities_combines_reports_and_overrides(monkeypatch):
    from piyo.models.clients import ModelInfo

    async def fake_list(provider):
        return [
            ModelInfo(id="m", tools=False, vision=True, context_length=64_000, max_output=8192),
            ModelInfo(id="plain"),
        ]

    monkeypatch.setattr(server, "list_models", fake_list)
    client = TestClient(server.create_app(TOKEN))
    assert client.get("/api/providers/ollama/models", headers=AUTH).status_code == 200
    url = "/api/model-capabilities"

    caps = client.get(url, params={"provider": "ollama", "model": "m"}, headers=AUTH).json()
    assert caps["tools_reported"] is False and caps["tools"]["effective"] == "prompt"
    assert caps["vision"]["effective"] is True and caps["vision"]["reported"] is True
    assert caps["context"]["context_limit"] == 64_000 and caps["context"]["custom"] is False
    assert caps["output"]["output_limit"] == 8192

    # Nothing reported: unknown stays unknown, defaults apply.
    unknown = client.get(url, params={"provider": "ollama", "model": "plain"}, headers=AUTH).json()
    assert unknown["tools_reported"] is None and unknown["tools"]["effective"] == "native"
    assert unknown["vision"]["effective"] is None and unknown["context"]["reported"] is None

    # Your setting beats the provider's report, and shows up here.
    q = {"provider": "ollama", "model": "m"}
    client.put("/api/vision", json={**q, "mode": "no"}, headers=AUTH)
    client.put("/api/tool-mode", json={**q, "mode": "native"}, headers=AUTH)
    changed = client.get(url, params=q, headers=AUTH).json()
    assert changed["vision"]["effective"] is False and changed["tools"]["effective"] == "native"

    assert client.get(url, params=q).status_code == 401
    assert client.get(url, params={"provider": "nope", "model": "m"}, headers=AUTH).status_code == 404
