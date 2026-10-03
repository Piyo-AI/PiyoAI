import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from piyo.server import app as server

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


def test_ws_streams_reply(client, monkeypatch):
    async def fake_stream(provider, model, messages, system=None):
        assert messages[-1].content == "hi"
        for part in ("Hel", "lo"):
            yield part

    monkeypatch.setattr(server, "stream_chat", fake_stream)
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json({"provider": "ollama", "model": "m",
                      "messages": [{"role": "user", "content": "hi"}]})
        events = [ws.receive_json() for _ in range(3)]
    assert events == [{"type": "delta", "text": "Hel"}, {"type": "delta", "text": "lo"},
                      {"type": "done"}]


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
