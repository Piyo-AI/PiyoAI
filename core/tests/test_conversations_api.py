import asyncio

import pytest
from fastapi.testclient import TestClient

from piyo.models.turn import TextDelta, ToolCall, TurnDone
from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.tools import Tool

TOKEN = "tok"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

SKILL = """---
name: helper
description: Helps.
requires:
  tools: [helper.run]
---
Use helper.run.
"""


class Recorder:
    """Fake model: plays scripted turns and records the history and tools it was given."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.calls: list[dict] = []

    async def __call__(self, provider, model, messages, tools, system, max_tokens):
        self.calls.append({"messages": [m.model_copy() for m in messages],
                           "tools": [t.name for t in tools]})
        for event in self.turns.pop(0):
            yield event


def say(text):
    return [TextDelta(text), TurnDone(text=text)]


def tool_turn(call_id, tool, **args):
    return [TurnDone(tool_calls=[ToolCall(id=call_id, name=tool, arguments=args)])]


def send(ws, text, conversation_id=None):
    payload = {"provider": "ollama", "model": "m", "message": text}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    ws.send_json(payload)
    events = []
    while True:
        event = ws.receive_json()
        events.append(event)
        if event["type"] in ("done", "error"):
            return events


def conv_id(events):
    return next(e["id"] for e in events if e["type"] == "conversation")


@pytest.fixture
def skills(tmp_path):
    d = tmp_path / "u" / "helper"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL, encoding="utf-8")
    return SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "u")


def make_client(recorder, skills=None, extra_tools=()):
    app = server.create_app(TOKEN, skills=skills, turn_fn=recorder)
    for tool in extra_tools:
        app.state.tools.register(tool)
    return TestClient(app)


def test_second_message_sees_earlier_tool_calls_and_results():
    rec = Recorder(
        tool_turn("c1", "current_time"), say("It is late."), say("Same as before."))
    client = make_client(rec)
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        first = send(ws, "what time is it?")
        cid = conv_id(first)
        send(ws, "and now?", cid)
    history = rec.calls[2]["messages"]
    assert [m.role for m in history] == ["user", "assistant", "tool", "assistant", "user"]
    assert history[1].tool_calls[0].name == "current_time"
    assert history[2].tool_call_id == "c1" and history[2].content
    assert history[-1].content == "and now?"


def test_loaded_skills_stay_active_in_later_messages(skills):
    async def run(args, ctx):
        return "ran"

    helper = Tool("helper.run", "Run", run)
    rec = Recorder(
        tool_turn("c1", "load_skill", name="helper"), say("loaded"), say("next"))
    client = make_client(rec, skills, [helper])
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        cid = conv_id(send(ws, "load the helper"))
        assert "helper__run" not in rec.calls[0]["tools"]
        assert "helper__run" in rec.calls[1]["tools"]
        send(ws, "use it", cid)
    assert "helper__run" in rec.calls[2]["tools"]
    detail = client.get(f"/api/conversations/{cid}", headers=AUTH).json()
    assert detail["active_skills"] == ["helper"]


def test_history_is_saved_before_done_and_listed():
    client = make_client(Recorder(say("Hello!")))
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        events = send(ws, "Say hello to me")
        # the run is saved by the time "done" arrives
        listing = client.get("/api/conversations", headers=AUTH).json()
    cid = conv_id(events)
    assert [(c["id"], c["title"]) for c in listing] == [(cid, "Say hello to me")]
    detail = client.get(f"/api/conversations/{cid}", headers=AUTH).json()
    assert [(m["role"], m["content"]) for m in detail["messages"]] == [
        ("user", "Say hello to me"),
        ("assistant", "Hello!"),
    ]


def test_stopping_mid_tool_leaves_a_valid_history():
    async def slow(args, ctx):
        await asyncio.sleep(30)
        return "never"

    rec = Recorder(tool_turn("c1", "slow"), say("recovered"))
    client = make_client(rec, extra_tools=[Tool("slow", "Slow", slow, core=True)])
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json({"provider": "ollama", "model": "m", "message": "go"})
        cid = ws.receive_json()["id"]
        assert ws.receive_json()["type"] == "tool_start"
        ws.send_json({"type": "cancel"})
        assert ws.receive_json() == {"type": "done", "reason": "cancelled"}
        send(ws, "try again", cid)
    history = rec.calls[1]["messages"]
    roles = [m.role for m in history]
    assert roles == ["user", "assistant", "tool", "user"]
    assert history[2].tool_call_id == "c1" and history[2].is_error


def test_unknown_conversation_id_is_reported():
    client = make_client(Recorder())
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        events = send(ws, "hi", "does-not-exist")
    assert events == [{"type": "error", "message": "That conversation no longer exists."}]


def test_rest_rename_delete_and_auth():
    client = make_client(Recorder(say("ok")))
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        cid = conv_id(send(ws, "hello"))
    url = f"/api/conversations/{cid}"
    assert client.get(url).status_code == 401
    assert client.get("/api/conversations").status_code == 401
    r = client.patch(url, headers=AUTH, json={"title": "Greetings"})
    assert r.status_code == 200 and r.json()["title"] == "Greetings"
    assert client.delete(url, headers=AUTH).status_code == 204
    for call in (client.get, client.delete):
        assert call(url, headers=AUTH).status_code == 404
    assert client.patch(url, headers=AUTH, json={"title": "x"}).status_code == 404
    assert client.get("/api/conversations", headers=AUTH).json() == []


def test_history_survives_a_restart(tmp_path):
    from piyo.store import ConversationStore

    path = tmp_path / "keep.db"
    first = TestClient(server.create_app(
        TOKEN, turn_fn=Recorder(say("one")), store=ConversationStore(path)))
    with first.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        cid = conv_id(send(ws, "remember this"))
    rec = Recorder(say("two"))
    second = TestClient(server.create_app(TOKEN, turn_fn=rec, store=ConversationStore(path)))
    with second.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        send(ws, "still there?", cid)
    assert [m.content for m in rec.calls[0]["messages"]] == [
        "remember this", "one", "still there?"]
