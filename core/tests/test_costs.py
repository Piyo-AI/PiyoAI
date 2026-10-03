import sqlite3

import pytest
from fastapi.testclient import TestClient
from test_runs import AUTH, TOKEN, chat, runs, scripted

from piyo.config.model_prices import ModelPrices, Price
from piyo.models import parse_openrouter_models
from piyo.models.turn import TurnDone
from piyo.server import app as server
from piyo.store import ConversationStore, RunLog, RunStore
from piyo.store.conversations import RUNS_SCHEMA, SCHEMA


def test_cost_is_tokens_times_price_per_million():
    log = RunLog("c", "p", "m", "x", price=Price(3.0, 15.0))
    log.add_tokens(200_000, 10_000, estimated=False)
    assert log.cost_usd == pytest.approx(0.6 + 0.15)
    assert RunLog("c", "p", "m", "x").cost_usd is None  # unknown price: no number, not zero


def test_openrouter_prices_are_per_million_and_variable_is_unknown():
    payload = {
        "data": [
            {"id": "a", "pricing": {"prompt": "0.000003", "completion": "0.000015"}},
            {"id": "b", "pricing": {"prompt": "-1", "completion": "-1"}},
            {"id": "c"},
        ]
    }
    a, b, c = parse_openrouter_models(payload)
    assert (a.price_input, a.price_output) == (pytest.approx(3.0), pytest.approx(15.0))
    assert b.price_input is None and c.price_output is None


def test_price_precedence_custom_then_reported_and_local_is_free(tmp_path):
    prices = ModelPrices(tmp_path / "p.json")
    reported = Price(1.0, 2.0)
    assert prices.resolve("p", "m", reported) == reported
    assert prices.resolve("p", "m") is None
    prices.set("p", "m", Price(5.0, 6.0))
    assert prices.resolve("p", "m", reported) == Price(5.0, 6.0)
    assert prices.resolve("p", "m", reported, local=True) == Price(0.0, 0.0)
    prices.set("p", "m", None)
    assert prices.get("p", "m") is None
    with pytest.raises(ValueError):
        prices.set("p", "m", Price(-1.0, 1.0))


def test_a_v2_database_gains_the_cost_column(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA + RUNS_SCHEMA)
    db.execute("PRAGMA user_version = 2")
    db.execute("INSERT INTO conversations VALUES ('c','t','n','n','[]')")
    db.execute("INSERT INTO runs VALUES ('r','c','n','p','m','req','done',NULL,5,1,2,0)")
    db.commit()
    db.close()
    conversations = ConversationStore(path)
    store = RunStore(conversations)
    assert store.get("r").cost_usd is None  # old runs keep working, cost unknown
    log = RunLog("c", "p", "m", "x", price=Price(1.0, 1.0))
    log.add_tokens(1_000_000, 0, estimated=False)
    store.save(log, "done")
    assert store.get(log.id).cost_usd == pytest.approx(1.0)


def test_local_runs_are_free_and_cloud_runs_without_a_price_have_no_cost():
    turn = [TurnDone(text="ok", input_tokens=10, output_tokens=5)]
    client = TestClient(server.create_app(TOKEN, turn_fn=scripted(turn)))
    chat(client)  # the test helper uses the local ollama provider
    assert runs(client)[0]["cost_usd"] == 0.0

    client = TestClient(server.create_app(TOKEN, turn_fn=scripted(turn)))
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json({"provider": "openai", "model": "m", "message": "hi"})
        ws.receive_json()  # conversation
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    assert runs(client)[0]["cost_usd"] is None


def test_price_api_sets_and_clears_a_custom_price():
    client = TestClient(server.create_app(TOKEN))
    url = "/api/price"
    q = {"provider": "openai", "model": "m"}
    assert client.get(url, params=q, headers=AUTH).json()["source"] == "unknown"
    put = client.put(url, json={**q, "input": 2.5, "output": 10}, headers=AUTH).json()
    assert put == {"input": 2.5, "output": 10.0, "source": "custom"}
    assert client.put(url, json={**q, "input": 1}, headers=AUTH).status_code == 400
    assert client.put(url, json={**q, "input": -1, "output": 1}, headers=AUTH).status_code == 400
    assert client.put(url, json=q, headers=AUTH).json()["source"] == "unknown"
    local = client.get(url, params={"provider": "ollama", "model": "m"}, headers=AUTH).json()
    assert local == {"input": 0.0, "output": 0.0, "source": "local"}
