import json

import httpx
import pytest
from fastapi.testclient import TestClient

from piyo.models.ollama import RECOMMENDED_MODEL, OllamaSetup
from piyo.server.app import create_app

LOCAL = "http://localhost:11434/v1"


def fake_ollama(models: list[str] | None, pull_lines: list[dict] | None = None, down: bool = False):
    """An Ollama that answers /api/tags and /api/pull from fixed data."""

    def handler(request: httpx.Request) -> httpx.Response:
        if down:
            raise httpx.ConnectError("refused")
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models or []]})
        if request.url.path == "/api/pull":
            body = "".join(json.dumps(line) + "\n" for line in pull_lines or [])
            return httpx.Response(200, content=body.encode())
        return httpx.Response(404)

    return OllamaSetup(transport=httpx.MockTransport(handler))


async def test_missing_when_nothing_answers():
    status = await fake_ollama(None, down=True).status(LOCAL)
    assert status.state == "missing"
    assert "not running" in status.message


async def test_running_without_models():
    assert (await fake_ollama([]).status(LOCAL)).state == "no_models"


async def test_ready_lists_models_sorted():
    status = await fake_ollama(["qwen3:4b", "llama3.2:3b"]).status(LOCAL)
    assert (status.state, status.models) == ("ready", ["llama3.2:3b", "qwen3:4b"])


async def test_only_this_computer_is_contacted():
    # A provider pointing at another host is never contacted: setup cannot be aimed at someone else's server.
    def boom(request):
        raise AssertionError("no request should be made")

    setup = OllamaSetup(transport=httpx.MockTransport(boom))
    assert (await setup.status("http://example.com:11434/v1")).state == "missing"
    with pytest.raises(ValueError, match="this computer"):
        setup.start_pull("http://192.168.1.5:11434/v1", RECOMMENDED_MODEL)


async def test_pull_reports_progress_then_the_real_list():
    lines = [
        {"status": "pulling manifest"},
        {"status": "pulling a", "digest": "a", "total": 100, "completed": 50},
        {"status": "pulling b", "digest": "b", "total": 100, "completed": 100},
        {"status": "success"},
    ]
    setup = fake_ollama([RECOMMENDED_MODEL], lines)
    assert setup.start_pull(LOCAL, RECOMMENDED_MODEL).state == "pulling"
    await setup._task
    # Finished: status falls back to reading the model list.
    assert (await setup.status(LOCAL)).state == "ready"


async def test_pull_failure_is_reported_and_readable():
    setup = fake_ollama([], [{"error": "pull model manifest: file does not exist"}])
    setup.start_pull(LOCAL, "nope:1b")
    await setup._task
    status = await setup.status(LOCAL)
    assert status.state == "failed"
    assert "nope:1b" in status.message and "file does not exist" in status.message


async def test_bad_model_names_are_refused():
    setup = fake_ollama([])
    for name in ("", "-rm", "a b", "x" * 200, "../etc"):
        with pytest.raises(ValueError, match="valid model name"):
            setup.start_pull(LOCAL, name)


async def test_second_pull_while_running_changes_nothing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    setup = OllamaSetup(transport=httpx.MockTransport(handler))
    first = setup.start_pull(LOCAL, RECOMMENDED_MODEL)
    second = setup.start_pull(LOCAL, "other:1b")
    assert first is second
    await setup._task


def test_onboarding_flag_round_trip_and_api():
    client = TestClient(create_app("tok", start_scheduler=False, ollama=fake_ollama([])))
    h = {"Authorization": "Bearer tok"}
    assert client.get("/api/onboarding", headers=h).json() == {"done": False}
    assert client.put("/api/onboarding", headers=h, json={"done": True}).json() == {"done": True}
    assert client.get("/api/onboarding", headers=h).json() == {"done": True}
    assert client.get("/api/onboarding").status_code == 401


def test_ollama_api_uses_the_preset_and_the_recommended_model():
    client = TestClient(create_app("tok", start_scheduler=False, ollama=fake_ollama(["llama3.2:3b"])))
    h = {"Authorization": "Bearer tok"}
    body = client.get("/api/ollama", headers=h).json()
    assert body["state"] == "ready" and body["recommended"] == RECOMMENDED_MODEL
    bad = client.post("/api/ollama/pull", headers=h, json={"model": "a b"})
    assert bad.status_code == 400
    assert client.get("/api/ollama").status_code == 401
