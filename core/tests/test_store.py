import sqlite3

import pytest

from piyo.config import data_dir
from piyo.models.turn import Message, ToolCall
from piyo.store import ConversationStore, UnknownConversation, complete_tool_calls
from piyo.store.conversations import DEFAULT_TITLE, TITLE_CHARS


@pytest.fixture
def store(tmp_path):
    return ConversationStore(tmp_path / "t.db")


def user(text):
    return Message(role="user", content=text)


def test_roundtrip_keeps_tool_calls_and_results(store):
    conv = store.create()
    msgs = [
        user("organise my downloads"),
        Message(
            role="assistant",
            content="On it",
            tool_calls=[ToolCall(id="c1", name="files__list", arguments={"path": "/tmp/x"})],
        ),
        Message(role="tool", content="a.txt", tool_call_id="c1", is_error=False),
        Message(role="assistant", content="Done"),
    ]
    store.append(conv.id, msgs[:2])
    store.append(conv.id, msgs[2:])
    got = store.get(conv.id)
    assert got.messages == msgs
    assert got.messages[1].tool_calls[0].arguments == {"path": "/tmp/x"}


def test_title_comes_from_first_user_message_once(store):
    conv = store.create()
    store.append(conv.id, [user("  Plan   my\nday please  ")])
    assert store.get(conv.id).title == "Plan my day please"
    store.append(conv.id, [user("something else")])
    assert store.get(conv.id).title == "Plan my day please"


def test_long_title_is_cut(store):
    conv = store.create()
    store.append(conv.id, [user("word " * 100)])
    assert len(store.get(conv.id).title) <= TITLE_CHARS


def test_rename_blank_falls_back(store):
    conv = store.create()
    store.rename(conv.id, "Taxes 2026")
    assert store.get(conv.id).title == "Taxes 2026"
    store.rename(conv.id, "   ")
    assert store.get(conv.id).title == DEFAULT_TITLE


def test_list_newest_activity_first(store):
    a, b = store.create(), store.create()
    store.append(a.id, [user("first")])
    assert [c.id for c in store.list()] == [a.id, b.id]
    store.append(b.id, [user("second")])
    assert [c.id for c in store.list()] == [b.id, a.id]


def test_active_skills_persist(store):
    conv = store.create()
    store.set_active_skills(conv.id, ["web-reader", "downloads-organizer", "web-reader"])
    assert store.get(conv.id).active_skills == ["downloads-organizer", "web-reader"]


def test_delete_removes_messages(store, tmp_path):
    conv = store.create()
    store.append(conv.id, [user("hi")])
    store.delete(conv.id)
    with pytest.raises(UnknownConversation):
        store.get(conv.id)
    with sqlite3.connect(tmp_path / "t.db") as db:
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_unknown_ids(store):
    for call in (
        lambda: store.get("nope"),
        lambda: store.append("nope", [user("x")]),
        lambda: store.rename("nope", "x"),
        lambda: store.delete("nope"),
    ):
        with pytest.raises(UnknownConversation):
            call()


def test_survives_reopening(tmp_path):
    first = ConversationStore(tmp_path / "t.db")
    conv = first.create()
    first.append(conv.id, [user("remember me")])
    assert ConversationStore(tmp_path / "t.db").get(conv.id).messages[0].content == "remember me"


def test_newer_database_is_refused_with_a_clear_message(tmp_path):
    path = tmp_path / "t.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="newer version"):
        ConversationStore(path).list()


def test_default_path_uses_data_dir():
    ConversationStore().create()
    assert (data_dir() / "piyo.db").exists()


def test_complete_tool_calls_fills_gaps_only():
    calls = [ToolCall(id="a", name="x"), ToolCall(id="b", name="y")]
    interrupted = [
        user("go"),
        Message(role="assistant", tool_calls=calls),
        Message(role="tool", content="ok", tool_call_id="a"),
    ]
    fixed = complete_tool_calls(interrupted)
    assert [m.tool_call_id for m in fixed if m.role == "tool"] == ["a", "b"]
    assert fixed[-1].is_error and "stopped" in fixed[-1].content
    assert fixed[:3] == interrupted
    assert complete_tool_calls(fixed) == fixed
    plain = [user("hi"), Message(role="assistant", content="hello")]
    assert complete_tool_calls(plain) == plain


def test_timestamps_never_tie_even_within_one_millisecond(monkeypatch):
    from datetime import UTC, datetime

    from piyo.store import conversations

    frozen = datetime(2030, 1, 1, tzinfo=UTC)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(conversations, "datetime", Frozen)
    monkeypatch.setattr(conversations, "_last_now", conversations._last_now)  # restored afterwards
    stamps = [conversations._now() for _ in range(3)]
    assert stamps == sorted(set(stamps))


def test_list_pages_without_gaps_or_repeats(store):
    ids = [store.create().id for _ in range(5)]  # newest last created
    newest_first = ids[::-1]
    assert [c.id for c in store.list(2, 0)] == newest_first[:2]
    assert [c.id for c in store.list(2, 2)] == newest_first[2:4]
    assert [c.id for c in store.list(2, 4)] == newest_first[4:]
    assert store.list(2, 10) == []
    assert [c.id for c in store.list()] == newest_first


def test_conversations_api_paginates(tmp_path):
    from fastapi.testclient import TestClient
    from test_server import AUTH, TOKEN

    from piyo.server import app as server

    store = ConversationStore(tmp_path / "api.db")
    client = TestClient(server.create_app(TOKEN, store=store))
    ids = [store.create().id for _ in range(7)][::-1]
    page = lambda **q: [c["id"] for c in client.get("/api/conversations", params=q, headers=AUTH).json()]  # noqa: E731
    assert page(limit=3) == ids[:3]
    assert page(limit=3, offset=3) == ids[3:6]
    assert page(limit=3, offset=6) == ids[6:]
    assert page(limit=0) == ids[:1]  # clamped to at least one
