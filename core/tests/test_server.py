import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from piyo.models.turn import TextDelta, ToolCall, TurnDone
from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.tools import Risk, Tool

MAILER_SKILL = """---
name: mailer
description: Mail.
requires:
  tools: [mail.send]
---
Send it.
"""

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client():
    return TestClient(server.create_app(TOKEN))


def test_health_is_public(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_requires_token(client):
    assert client.get("/api/providers").status_code == 401
    assert client.get("/api/providers", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_list_and_set_key(client):
    providers = {p["id"]: p for p in client.get("/api/providers", headers=AUTH).json()}
    assert providers["anthropic"]["has_key"] is False
    assert client.put("/api/providers/anthropic/key", json={"key": " sk-x "},
                      headers=AUTH).status_code == 204
    providers = {p["id"]: p for p in client.get("/api/providers", headers=AUTH).json()}
    assert providers["anthropic"]["has_key"] is True
    assert "sk-x" not in str(providers)  # keys are never returned


def test_add_custom_provider(client):
    body = {"id": "together", "name": "Together", "api_style": "openai",
            "base_url": "https://api.together.xyz/v1"}
    assert client.post("/api/providers", json=body, headers=AUTH).status_code == 201
    assert client.post("/api/providers", json=body, headers=AUTH).status_code == 409
    assert client.delete("/api/providers/anthropic", headers=AUTH).status_code == 400
    assert client.delete("/api/providers/together", headers=AUTH).status_code == 204


def test_ws_rejects_bad_token(client):
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/chat?token=bad") as ws:
        ws.receive_json()


def scripted(*turns):
    """A fake model turn function that plays one list of events per model call."""
    queue = list(turns)

    async def turn_fn(provider, model, messages, tools, system, max_tokens):
        for event in queue.pop(0):
            yield event

    return turn_fn


def chat_request(text="hi"):
    return {"provider": "ollama", "model": "m", "messages": [{"role": "user", "content": text}]}


def test_ws_streams_reply():
    turn_fn = scripted([TextDelta("Hel"), TextDelta("lo"), TurnDone(text="Hello")])
    client = TestClient(server.create_app(TOKEN, turn_fn=turn_fn))
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json(chat_request())
        events = [ws.receive_json() for _ in range(3)]
    assert events == [
        {"type": "delta", "text": "Hel"},
        {"type": "delta", "text": "lo"},
        {"type": "done", "reason": "done"},
    ]


def test_ws_reports_tool_calls():
    turn_fn = scripted(
        [TurnDone(tool_calls=[ToolCall(id="c1", name="current_time")])],
        [TurnDone(text="ok")],
    )
    client = TestClient(server.create_app(TOKEN, turn_fn=turn_fn))
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json(chat_request())
        start, end, done = (ws.receive_json() for _ in range(3))
    assert start == {"type": "tool_start", "id": "c1", "name": "current_time", "arguments": {}}
    assert end["type"] == "tool_end" and end["is_error"] is False and end["output"]
    assert done == {"type": "done", "reason": "done"}


@pytest.mark.parametrize("approve", [True, False])
def test_ws_approval_round_trip(approve, tmp_path):
    sent = []

    async def send(args, ctx):
        sent.append(args)
        return "sent"

    skills = SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "u")
    (tmp_path / "u" / "mailer").mkdir(parents=True)
    (tmp_path / "u" / "mailer" / "SKILL.md").write_text(MAILER_SKILL, encoding="utf-8")
    skills.reload()
    turn_fn = scripted(
        [TurnDone(tool_calls=[ToolCall(id="c1", name="load_skill", arguments={"name": "mailer"})])],
        [TurnDone(tool_calls=[ToolCall(id="c2", name="mail.send", arguments={"to": "a@b.c"})])],
        [TurnDone(text="ok")],
    )
    app = server.create_app(TOKEN, skills=skills, turn_fn=turn_fn)
    # Register the tool the skill unlocks; real integrations will do this at startup.
    server_tools = app.state.tools
    server_tools.register(Tool("mail.send", "Send", send, risk=Risk.CONFIRM))
    with TestClient(app).websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json(chat_request())
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "approval_request":
                assert event["tool"] == "mail.send" and event["arguments"] == {"to": "a@b.c"}
                ws.send_json({"type": "approval", "id": event["id"], "approve": approve})
            if event["type"] == "done":
                break
    assert sent == ([{"to": "a@b.c"}] if approve else [])
    ends = [e for e in events if e["type"] == "tool_end"]
    assert ends[-1]["is_error"] is (not approve)


def test_ws_cancel_stops_the_run():
    async def slow(provider, model, messages, tools, system, max_tokens):
        yield TextDelta("working")
        await asyncio.sleep(30)
        yield TurnDone(text="never")

    client = TestClient(server.create_app(TOKEN, turn_fn=slow))
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json(chat_request())
        assert ws.receive_json() == {"type": "delta", "text": "working"}
        ws.send_json({"type": "cancel"})
        assert ws.receive_json() == {"type": "done", "reason": "cancelled"}


def test_ws_cancel_when_idle_still_acknowledges():
    client = TestClient(server.create_app(TOKEN, turn_fn=scripted()))
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json({"type": "cancel"})
        assert ws.receive_json() == {"type": "done", "reason": "cancelled"}


def test_skills_endpoint(tmp_path):
    (tmp_path / "u" / "mailer").mkdir(parents=True)
    (tmp_path / "u" / "mailer" / "SKILL.md").write_text(MAILER_SKILL, encoding="utf-8")
    (tmp_path / "u" / "broken").mkdir()
    skills = SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "u")
    client = TestClient(server.create_app(TOKEN, skills=skills))
    body = client.get("/api/skills", headers=AUTH).json()
    assert [(s["name"], s["tools"], s["enabled"]) for s in body["skills"]] == [
        ("mailer", ["mail.send"], True)
    ]
    assert "user/broken" in body["errors"]
    assert client.get("/api/skills").status_code == 401


def test_ws_reports_missing_key(client):
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json({"provider": "openai", "model": "m",
                      "messages": [{"role": "user", "content": "hi"}]})
        event = ws.receive_json()
    assert event["type"] == "error" and "API key" in event["message"]


@pytest.mark.parametrize("origin", ["http://127.0.0.1:1420", "http://localhost:1420",
                                    "http://tauri.localhost", "tauri://localhost"])
def test_cors_allows_app_origins(client, origin):
    r = client.options("/api/providers", headers={"Origin": origin,
                                                 "Access-Control-Request-Method": "GET",
                                                 "Access-Control-Request-Headers": "authorization"})
    assert r.headers.get("access-control-allow-origin") == origin


def test_cors_rejects_other_origins(client):
    r = client.options("/api/providers", headers={"Origin": "https://evil.example",
                                                 "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers


def test_ws_unreachable_local_provider_fails_fast(client):
    """Ollama not running: a readable error within seconds, not a hang."""
    import time

    from piyo.models import ProviderUpdate

    reg = server.ProviderRegistry()
    reg.update("ollama", ProviderUpdate(base_url="http://127.0.0.1:9/v1"))
    c = TestClient(server.create_app(TOKEN, reg))
    start = time.monotonic()
    with c.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json({"provider": "ollama", "model": "m",
                      "messages": [{"role": "user", "content": "hi"}]})
        event = ws.receive_json()
    assert event["type"] == "error"
    assert "Couldn't connect to Ollama" in event["message"] and "Is it running?" in event["message"]
    assert time.monotonic() - start < 15


def test_openrouter_lists_models_without_a_key(client):
    providers = {p["id"]: p for p in client.get("/api/providers", headers=AUTH).json()}
    assert providers["openrouter"]["public_models"] is True
    assert providers["openai"]["public_models"] is False
