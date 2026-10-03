import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from piyo.agent import Agent, ToolFinished
from piyo.agent.loop import MAX_IDENTICAL_FAILURES
from piyo.config.folders import ApprovedFolders
from piyo.models.turn import Message, ToolCall, TurnDone
from piyo.safety import PermissionGate
from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.store import AuditStore, ConversationStore
from piyo.tools import Risk, Tool, ToolRegistry, core_tools
from piyo.tools.files import file_tools

from test_agent import PROVIDER, Script, call
from test_runs import AUTH, TOKEN, runs


def agent_with(tools, script, gate=None):
    skills = SkillRegistry(builtin_dir=None, user_dir=None)
    return Agent(PROVIDER, "m", ToolRegistry(core_tools() + tools), skills, gate, turn_fn=script)


async def drain(agent):
    return [e async for e in agent.run([Message(role="user", content="go")])]


def failing_tool(counter):
    async def boom(args, ctx):
        counter.append(args)
        raise RuntimeError("nope")

    return Tool("flaky.op", "x", boom, core=True)


async def test_identical_failing_call_is_refused_after_three_tries():
    ran = []
    same = [call("flaky.op", f"c{i}", a=1) for i in range(5)]
    script = Script(*[[TurnDone(tool_calls=[c])] for c in same], [TurnDone(text="gave up")])
    events = await drain(agent_with([failing_tool(ran)], script))
    outputs = [e.output for e in events if isinstance(e, ToolFinished)]
    assert len(ran) == MAX_IDENTICAL_FAILURES  # the 4th and 5th never ran
    assert "not run again" in outputs[3] and "not run again" in outputs[4]


async def test_different_arguments_are_not_counted_together():
    ran = []
    calls = [call("flaky.op", f"c{i}", a=i) for i in range(5)]
    script = Script(*[[TurnDone(tool_calls=[c])] for c in calls], [TurnDone(text="done")])
    await drain(agent_with([failing_tool(ran)], script))
    assert len(ran) == 5


async def test_a_declined_call_is_not_asked_again():
    sent, asked = [], []

    async def send(args, ctx):
        sent.append(args)
        return "sent"

    async def deny(req):
        asked.append(req)
        return False

    tool = Tool("mail.send", "s", send, risk=Risk.CONFIRM, core=True)
    script = Script(
        [TurnDone(tool_calls=[call("mail.send", "c1", to="a")])],
        [TurnDone(tool_calls=[call("mail.send", "c2", to="a")])],
        [TurnDone(tool_calls=[call("mail.send", "c3", to="b")])],  # different: asks again
        [TurnDone(text="ok")],
    )
    events = await drain(agent_with([tool], script, PermissionGate(deny)))
    out = [e.output for e in events if isinstance(e, ToolFinished)]
    assert len(asked) == 2 and sent == []
    assert "already declined" in out[1]


async def test_approval_request_carries_the_summary_and_the_model_text():
    seen = []

    async def yes(req):
        seen.append(req)
        return True

    async def noop(args, ctx):
        return "ok"

    tool = Tool("x.go", "g", noop, risk=Risk.CONFIRM, core=True, summarize=lambda a: f"Do {a['n']}")
    script = Script(
        [TurnDone(text="Tidying as you asked.", tool_calls=[call("x.go", n=7)])], [TurnDone(text="k")]
    )
    await drain(agent_with([tool], script, PermissionGate(yes)))
    assert seen[0].summary == "Do 7" and seen[0].why == "Tidying as you asked."


def test_summary_falls_back_when_the_tool_has_none_or_it_breaks():
    async def noop(args, ctx):
        return ""

    assert Tool("a.b", "", noop).summary_of({"x": 1}) == "Run a.b with x: 1"
    broken = Tool("a.b", "", noop, summarize=lambda a: a["missing"])
    assert broken.summary_of({}) == "Run a.b"


def test_file_tool_summaries_name_the_real_paths(tmp_path):
    tools = {t.name: t for t in file_tools(ApprovedFolders(tmp_path / "g.json"))}
    move = tools["files.move"].summary_of({"source": "/a/x", "destination": "/a/y"})
    assert move == "Move /a/x to /a/y"
    assert tools["files.delete"].summary_of({"path": "/a/x"}) == "Permanently delete /a/x"
    write = tools["files.write"].summary_of({"path": "/a/x", "content": "abc", "overwrite": True})
    assert write == "Replace the contents of /a/x (3 characters)"


# --- audit chain ------------------------------------------------------------------------------


@pytest.fixture
def audit(tmp_path):
    conversations = ConversationStore(tmp_path / "t.db")
    return conversations, AuditStore(conversations)


def add(audit_store, n, allowed=True):
    audit_store.record("r", "c", "files.delete", f"Delete {n}", {"path": f"/f{n}"}, "why", allowed)


def test_chain_verifies_and_lists_newest_first(audit):
    _, store = audit
    assert store.report().verified and store.report().entries == []
    for n in range(3):
        add(store, n, allowed=n != 1)
    report = store.report()
    assert report.verified and [e.seq for e in report.entries] == [3, 2, 1]
    assert [e.decision for e in report.entries] == ["allowed", "declined", "allowed"]
    assert report.entries[0].prev_hash == report.entries[1].hash


def test_editing_an_entry_is_detected(audit):
    conversations, store = audit
    for n in range(3):
        add(store, n)
    with sqlite3.connect(conversations.path) as db:
        body = json.loads(db.execute("SELECT body FROM audit WHERE seq = 2").fetchone()[0])
        body["decision"] = "declined"
        db.execute("UPDATE audit SET body = ? WHERE seq = 2", (json.dumps(body),))
    report = store.report()
    assert not report.verified and "Entry 2" in report.problem


def test_deleting_a_middle_or_last_entry_is_detected(audit):
    conversations, store = audit
    for n in range(4):
        add(store, n)
    with sqlite3.connect(conversations.path) as db:
        db.execute("DELETE FROM audit WHERE seq = 4")
    assert "removed from the end" in store.report().problem
    with sqlite3.connect(conversations.path) as db:
        db.execute("DELETE FROM audit WHERE seq = 2")
    assert not store.report().verified


def test_secrets_are_masked_but_the_digest_covers_the_exact_arguments(audit):
    _, store = audit
    store.record("r", "c", "t", "s", {"note": "key sk-abcdefghijklmnopqrstuvwxyz123456"}, "", True)
    other = {"note": "key sk-ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"}
    store.record("r", "c", "t", "s", other, "", True)
    a, b = reversed(store.report().entries)
    assert "sk-abcdef" not in json.dumps(a.arguments)
    assert a.arguments == b.arguments and a.args_digest != b.args_digest


def test_audit_survives_deleting_the_conversation_and_runs(tmp_path):
    conversations = ConversationStore(tmp_path / "t.db")
    cid = conversations.create().id
    store = AuditStore(conversations)
    store.record("r", cid, "t", "s", {}, "", True)
    conversations.delete(cid)
    assert len(store.report().entries) == 1 and store.report().verified


def test_audit_api_records_both_decisions_from_a_real_run():
    async def send(args, ctx):
        return "sent"

    def tc(id_, to):
        return ToolCall(id=id_, name="mail.send", arguments={"to": to})

    turns = [
        [TurnDone(text="Sending.", tool_calls=[tc("c1", "a@b.c")])],
        [TurnDone(tool_calls=[tc("c2", "x@y.z")])],
        [TurnDone(text="done")],
    ]

    async def turn_fn(provider, model, messages, tools, system, max_tokens):
        for e in turns.pop(0):
            yield e

    app = server.create_app(TOKEN, turn_fn=turn_fn)
    tool = Tool("mail.send", "Send", send, risk=Risk.CONFIRM, core=True, summarize=lambda a: f"Email {a['to']}")
    app.state.tools.register(tool)
    client = TestClient(app)
    answers = iter([True, False])
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json({"provider": "ollama", "model": "m", "message": "mail"})
        cards = []
        while True:
            event = ws.receive_json()
            if event["type"] == "approval_request":
                cards.append(event)
                ws.send_json({"type": "approval", "id": event["id"], "approve": next(answers)})
            if event["type"] == "done":
                break
    assert cards[0]["summary"] == "Email a@b.c" and cards[0]["why"] == "Sending."
    report = client.get("/api/audit", headers=AUTH).json()
    assert report["verified"] is True
    assert [(e["summary"], e["decision"]) for e in report["entries"]] == [
        ("Email x@y.z", "declined"),
        ("Email a@b.c", "allowed"),
    ]
    assert report["entries"][1]["run_id"] == runs(client)[0]["id"]
    assert client.get("/api/audit").status_code == 401
