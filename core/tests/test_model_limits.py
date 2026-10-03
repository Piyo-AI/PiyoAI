import pytest
from fastapi.testclient import TestClient

from piyo.config.model_limits import AUTO_CEILING, DEFAULT_OUTPUT_TOKENS, ModelLimits
from piyo.models import ModelInfo
from piyo.models.clients import parse_openrouter_models
from piyo.models.turn import TextDelta, TurnDone
from piyo.server import app as server

TOKEN = "tok"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def limits(tmp_path):
    return ModelLimits(tmp_path / "l.json")


def test_defaults_reported_and_ceiling(limits):
    assert limits.resolve("p", "m") == DEFAULT_OUTPUT_TOKENS
    assert limits.resolve("p", "m", 8192) == 8192
    assert limits.resolve("p", "m", 128_000) == AUTO_CEILING


def test_custom_wins_but_never_exceeds_a_known_maximum(limits):
    limits.set("p", "m", 30_000)
    assert limits.resolve("p", "m") == 30_000
    assert limits.resolve("p", "m", 8192) == 8192  # the model cannot do more than it says
    assert limits.resolve("p", "other") == DEFAULT_OUTPUT_TOKENS
    assert limits.resolve("q", "m") == DEFAULT_OUTPUT_TOKENS


def test_reset_and_bounds(limits):
    limits.set("p", "m", 5000)
    limits.set("p", "m", None)
    assert limits.get("p", "m") is None
    limits.set("p", "never-set", None)  # removing nothing is fine
    for bad in (0, 100, 10**7, -5):
        with pytest.raises(ValueError, match="between"):
            limits.set("p", "m", bad)


def test_corrupt_file_falls_back_to_defaults(limits):
    limits.path.write_text('{"p": {"m": "lots", "n": true, "o": -1}, "q": 5}', encoding="utf-8")
    assert limits.all() == {}
    limits.path.write_text("not json", encoding="utf-8")
    assert limits.resolve("p", "m") == DEFAULT_OUTPUT_TOKENS


def test_openrouter_reports_max_completion_tokens():
    payload = {
        "data": [
            {"id": "a/with", "top_provider": {"max_completion_tokens": 8192}},
            {"id": "a/null", "top_provider": {"max_completion_tokens": None}},
            {"id": "a/missing"},
            {"id": "a/junk", "top_provider": {"max_completion_tokens": "many"}},
        ]
    }
    by_id = {m.id: m.max_output for m in parse_openrouter_models(payload)}
    assert by_id == {"a/with": 8192, "a/null": None, "a/missing": None, "a/junk": None}


def run_chat(client, provider="ollama", model="m"):
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json({"provider": provider, "model": model, "message": "hi"})
        while ws.receive_json()["type"] != "done":
            pass


def capture():
    seen = []

    async def turn_fn(provider, model, messages, tools, system, max_tokens):
        seen.append(max_tokens)
        yield TextDelta("ok")
        yield TurnDone(text="ok")

    return seen, turn_fn


def put(client, **body):
    return client.put("/api/output-limit", headers=AUTH, json={"provider": "ollama", **body})


def test_chat_uses_default_then_custom_limit():
    seen, turn_fn = capture()
    client = TestClient(server.create_app(TOKEN, turn_fn=turn_fn))
    run_chat(client)
    r = put(client, model="m", tokens=12000)
    assert r.json() == {"output_limit": 12000, "custom": True, "reported_max": None}
    run_chat(client)
    run_chat(client, model="other")
    assert seen == [DEFAULT_OUTPUT_TOKENS, 12000, DEFAULT_OUTPUT_TOKENS]
    r = put(client, model="m", tokens=None)
    assert r.json()["custom"] is False and r.json()["output_limit"] == DEFAULT_OUTPUT_TOKENS


def test_reported_maximum_from_model_list_is_used_and_enforced(monkeypatch):
    async def fake_list(provider):
        return [ModelInfo(id="big/model", max_output=8192), ModelInfo(id="plain")]

    monkeypatch.setattr(server, "list_models", fake_list)
    seen, turn_fn = capture()
    client = TestClient(server.create_app(TOKEN, turn_fn=turn_fn))
    assert client.get("/api/providers/ollama/models", headers=AUTH).status_code == 200
    url = "/api/output-limit?provider=ollama&model=big/model"
    info = client.get(url, headers=AUTH).json()
    assert info == {"output_limit": 8192, "custom": False, "reported_max": 8192}
    run_chat(client, model="big/model")
    assert seen == [8192]
    too_big = put(client, model="big/model", tokens=9000)
    assert too_big.status_code == 400 and "at most 8192" in too_big.json()["detail"]


def test_limit_api_validation_and_auth():
    client = TestClient(server.create_app(TOKEN))
    body = {"provider": "ollama", "model": "m", "tokens": 50}
    assert client.put("/api/output-limit", json=body).status_code == 401
    assert put(client, model="m", tokens=50).status_code == 400
    missing = client.put("/api/output-limit", headers=AUTH, json={**body, "provider": "nope"})
    assert missing.status_code == 404
    assert put(client, model="  ", tokens=999).status_code == 400
    assert client.get("/api/output-limit?provider=nope&model=m", headers=AUTH).status_code == 404
