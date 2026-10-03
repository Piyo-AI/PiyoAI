import pytest
from fastapi.testclient import TestClient

from piyo.models.turn import TextDelta, ToolCall, TurnDone
from piyo.server import app as server
from piyo.store import ConversationStore, RunLog, RunStore, redact
from piyo.store.runs import MAX_RUNS, MAX_STRING, REDACTED
from piyo.tools import Risk, Tool

TOKEN = "tok"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SECRET = "sk-abcdefghijklmnopqrstuvwxyz123456"


def scripted(*turns):
    turns = list(turns)

    async def turn_fn(provider, model, messages, tools, system, max_tokens):
        for event in turns.pop(0):
            yield event

    return turn_fn


def chat(client, text="hi", conversation_id=None):
    payload = {"provider": "ollama", "model": "m", "message": text}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json(payload)
        while True:
            event = ws.receive_json()
            if event["type"] in ("done", "error"):
                return event


def runs(client, **params):
    return client.get("/api/runs", headers=AUTH, params=params).json()


# --- redaction ---------------------------------------------------------------------------------


def test_redact_masks_keys_tokens_and_assignments():
    out = redact(
        {
            "note": f"use {SECRET} now, Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
            "password": "hunter2hunter2",
            "nested": [{"api_key": "x"}, "token=abc12345 and fine"],
            "count": 3,
        }
    )
    assert SECRET not in str(out) and "hunter2" not in str(out) and "abc12345" not in str(out)
    assert out["password"] == REDACTED and out["nested"][0]["api_key"] == REDACTED
    assert out["count"] == 3 and "fine" in out["nested"][1]


def test_redact_known_secrets_and_long_text():
    assert redact("my key is plainsecretvalue", ("plainsecretvalue",)) == f"my key is {REDACTED}"
    cut = redact("a" * (MAX_STRING + 50))
    assert len(cut) < MAX_STRING + 80 and "cut" in cut


def test_short_known_secrets_are_ignored_so_words_survive():
    log = RunLog("c", "p", "m", "say yes", secrets=("yes",))
    assert log.request == "say yes"


# --- store -------------------------------------------------------------------------------------


@pytest.fixture
def stores(tmp_path):
    conversations = ConversationStore(tmp_path / "t.db")
    return conversations, RunStore(conversations)


def test_save_and_read_back(stores):
    conversations, runs_store = stores
    cid = conversations.create().id
    log = RunLog(cid, "p", "m", "do the thing")
    log.step("tool", 12, name="files.read", output="hello")
    log.add_tokens(100, 20, estimated=True)
    runs_store.save(log, "done")
    run = runs_store.get(log.id)
    assert run.outcome == "done" and run.input_tokens == 100 and run.tokens_estimated is True
    assert run.steps[0]["data"]["name"] == "files.read"
    assert runs_store.list(cid)[0].id == log.id
    assert runs_store.list("other") == []


def test_deleting_a_conversation_deletes_its_runs(stores):
    conversations, runs_store = stores
    cid = conversations.create().id
    log = RunLog(cid, "p", "m", "x")
    runs_store.save(log, "done")
    conversations.delete(cid)
    assert runs_store.list() == []


def test_run_for_a_deleted_conversation_is_dropped_quietly(stores):
    _, runs_store = stores
    runs_store.save(RunLog("gone", "p", "m", "x"), "cancelled")
    assert runs_store.list() == []


def test_old_runs_are_pruned(stores):
    conversations, runs_store = stores
    cid = conversations.create().id
    for _ in range(MAX_RUNS + 3):
        runs_store.save(RunLog(cid, "p", "m", "x"), "done")
    assert len(runs_store.list(limit=MAX_RUNS + 10)) == MAX_RUNS


# --- through the app ---------------------------------------------------------------------------


def test_run_records_turns_tools_tokens_and_outcome():
    turn_fn = scripted(
        [TurnDone(text="checking", tool_calls=[ToolCall(id="c1", name="current_time")],
                  input_tokens=50, output_tokens=7)],
        [TextDelta("ok"), TurnDone(text="ok", input_tokens=60, output_tokens=2)],
    )
    client = TestClient(server.create_app(TOKEN, turn_fn=turn_fn))
    assert chat(client, "what time is it")["reason"] == "done"
    (summary,) = runs(client)
    assert summary["outcome"] == "done" and summary["request"] == "what time is it"
    assert (summary["input_tokens"], summary["output_tokens"]) == (110, 9)
    assert summary["tokens_estimated"] is False
    detail = client.get(f"/api/runs/{summary['id']}", headers=AUTH).json()
    assert [s["kind"] for s in detail["steps"]] == ["model", "tool", "model"]
    tool = detail["steps"][1]
    assert tool["data"]["name"] == "current_time" and tool["data"]["is_error"] is False
    assert tool["duration_ms"] is not None


def test_tokens_are_estimated_when_the_provider_reports_none():
    client = TestClient(server.create_app(TOKEN, turn_fn=scripted([TurnDone(text="hello")])))
    chat(client, "hi")
    (summary,) = runs(client)
    assert summary["tokens_estimated"] is True
    assert summary["input_tokens"] > 0 and summary["output_tokens"] > 0


@pytest.mark.parametrize("approve", [True, False])
def test_approvals_are_logged_with_the_arguments_approved(approve):
    async def send(args, ctx):
        return "sent"

    turn_fn = scripted(
        [TurnDone(tool_calls=[ToolCall(id="c1", name="mail.send", arguments={"to": "a@b.c"})])],
        [TurnDone(text="ok")],
    )
    app = server.create_app(TOKEN, turn_fn=turn_fn)
    app.state.tools.register(Tool("mail.send", "Send", send, risk=Risk.CONFIRM, core=True))
    client = TestClient(app)
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json({"provider": "ollama", "model": "m", "message": "mail it"})
        while True:
            event = ws.receive_json()
            if event["type"] == "approval_request":
                ws.send_json({"type": "approval", "id": event["id"], "approve": approve})
            if event["type"] == "done":
                break
    detail = client.get(f"/api/runs/{runs(client)[0]['id']}", headers=AUTH).json()
    kinds = [s["kind"] for s in detail["steps"]]
    assert kinds == ["model", "approval", "tool", "model"]
    approval = detail["steps"][1]["data"]
    assert approval == {
        "tool": "mail.send",
        "arguments": {"to": "a@b.c"},
        "summary": "Run mail.send with to: a@b.c",
        "why": "",
        "approved": approve,
    }
    assert detail["steps"][2]["data"]["is_error"] is (not approve)


def test_secrets_never_reach_the_log():
    async def leak(args, ctx):
        return f"the key is {SECRET}"

    turn_fn = scripted(
        [TurnDone(tool_calls=[ToolCall(id="c1", name="leak.it", arguments={"password": "pw123456"})])],
        [TurnDone(text="done")],
    )
    app = server.create_app(TOKEN, turn_fn=turn_fn)
    app.state.tools.register(Tool("leak.it", "Leak", leak, core=True))
    client = TestClient(app)
    chat(client, f"here is my key {SECRET}")
    (summary,) = runs(client)
    dump = str(client.get(f"/api/runs/{summary['id']}", headers=AUTH).json())
    assert SECRET not in dump and "pw123456" not in dump and REDACTED in dump


def test_failed_run_is_logged_as_error():
    async def broken(provider, model, messages, tools, system, max_tokens):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    client = TestClient(server.create_app(TOKEN, turn_fn=broken))
    assert chat(client)["type"] == "error"
    (summary,) = runs(client)
    assert summary["outcome"] == "error" and "boom" in summary["error"]


def test_runs_filter_by_conversation_and_404_and_auth():
    turn_fn = scripted([TurnDone(text="a")], [TurnDone(text="b")])
    client = TestClient(server.create_app(TOKEN, turn_fn=turn_fn))
    chat(client, "one")
    chat(client, "two")
    first, second = runs(client)[1], runs(client)[0]
    assert first["conversation_id"] != second["conversation_id"]
    assert [r["id"] for r in runs(client, conversation_id=first["conversation_id"])] == [first["id"]]
    assert client.get("/api/runs/nope", headers=AUTH).status_code == 404
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/runs/x").status_code == 401


def test_cancelled_run_is_logged_as_cancelled():
    import asyncio

    async def hangs(provider, model, messages, tools, system, max_tokens):
        yield TextDelta("thinking")
        await asyncio.sleep(60)

    client = TestClient(server.create_app(TOKEN, turn_fn=hangs))
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json({"provider": "ollama", "model": "m", "message": "slow one"})
        while ws.receive_json()["type"] != "delta":
            pass
        ws.send_json({"type": "cancel"})
        assert ws.receive_json() == {"type": "done", "reason": "cancelled"}
    (summary,) = runs(client)
    assert summary["outcome"] == "cancelled"


def test_runs_and_audit_api_paginate():
    turn_fn = scripted([TurnDone(text="a")], [TurnDone(text="b")], [TurnDone(text="c")])
    client = TestClient(server.create_app(TOKEN, turn_fn=turn_fn))
    for text in ("one", "two", "three"):
        chat(client, text)
    everything = [r["id"] for r in runs(client)]
    assert len(everything) == 3
    first = client.get("/api/runs", params={"limit": 2}, headers=AUTH).json()
    rest = client.get("/api/runs", params={"limit": 2, "offset": 2}, headers=AUTH).json()
    assert [r["id"] for r in first + rest] == everything
    assert client.get("/api/audit", params={"limit": 1, "offset": 5}, headers=AUTH).json()["entries"] == []
