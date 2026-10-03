"""Conversations on disk (SQLite): the core owns the history, the app only shows it.

Each message is stored whole (tool calls and tool results included), so a restored conversation
can be sent back to the model exactly as it was. A conversation also remembers which skills were
loaded, because those unlock tools for the following messages.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from piyo.config import data_dir
from piyo.models.turn import Message

DEFAULT_TITLE = "New chat"
TITLE_CHARS = 60
SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active_skills TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE messages (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (conversation_id, seq)
);
"""

# v2: the task log (one row per agent run, one per step), see `piyo.store.runs`.
RUNS_SCHEMA = """
CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    request TEXT NOT NULL,
    outcome TEXT NOT NULL,
    error TEXT,
    duration_ms INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    tokens_estimated INTEGER NOT NULL
);
CREATE INDEX runs_by_start ON runs (started_at);
CREATE TABLE run_steps (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""

# v3: what a run cost in US dollars; NULL when the model's price is unknown.
RUNS_V3 = "ALTER TABLE runs ADD COLUMN cost_usd REAL"

# v4: the approval audit chain, see `piyo.store.audit`. No foreign keys: it outlives runs and chats.
AUDIT_SCHEMA = """
CREATE TABLE audit (
    seq INTEGER PRIMARY KEY,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    body TEXT NOT NULL
);
"""


class UnknownConversation(KeyError):
    pass


@dataclass
class ConversationSummary:
    id: str
    title: str
    created_at: str
    updated_at: str


@dataclass
class Conversation(ConversationSummary):
    messages: list[Message]
    active_skills: list[str]


_last_now = datetime.min.replace(tzinfo=UTC)
_now_lock = threading.Lock()


def _now() -> str:
    """Current time to the millisecond, never equal to or before the previous call.

    Lists sort by this, so two changes in the same millisecond must not tie.
    """
    global _last_now
    with _now_lock:
        t = datetime.now(UTC)
        t = t.replace(microsecond=t.microsecond // 1000 * 1000)
        if t <= _last_now:
            t = _last_now + timedelta(milliseconds=1)
        _last_now = t
    return t.isoformat(timespec="milliseconds")


def title_from(text: str) -> str:
    line = " ".join(text.split())
    if len(line) > TITLE_CHARS:
        line = line[: TITLE_CHARS - 1].rstrip() + "…"
    return line or DEFAULT_TITLE


def complete_tool_calls(messages: list[Message]) -> list[Message]:
    """Give every tool call an answer, so an interrupted run never leaves a dangling call.

    Providers reject a history where an assistant tool call has no matching tool result. A run that
    was stopped (or crashed) between the call and its result gets a placeholder result instead.
    """
    out: list[Message] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        out.append(msg)
        i += 1
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        answered = set()
        while i < len(messages) and messages[i].role == "tool":  # the results that did arrive
            answered.add(messages[i].tool_call_id)
            out.append(messages[i])
            i += 1
        for call in msg.tool_calls:
            if call.id not in answered:
                out.append(
                    Message(
                        role="tool",
                        content="This step did not finish because the run was stopped.",
                        tool_call_id=call.id,
                        is_error=True,
                    )
                )
    return out


class ConversationStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._ready: Path | None = None

    @property
    def path(self) -> Path:
        return self._path or data_dir() / "piyo.db"

    def db(self):
        """A connection in a transaction, for sibling stores (`RunStore`) in the same file."""
        return self._db()

    @contextmanager
    def _db(self):
        path = self.path
        with closing(sqlite3.connect(path, timeout=10)) as db:
            db.execute("PRAGMA foreign_keys = ON")
            if self._ready != path:
                self._migrate(db)
                self._ready = path
            with db:  # commit on success, roll back on error
                yield db

    @staticmethod
    def _migrate(db: sqlite3.Connection) -> None:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                "The conversation database was written by a newer version of Piyo. "
                "Update the app to open it."
            )
        if version < 1:
            db.executescript(SCHEMA)
        if version < 2:
            db.executescript(RUNS_SCHEMA)
        if version < 3:
            db.execute(RUNS_V3)
        if version < 4:
            db.executescript(AUDIT_SCHEMA)
        if version < SCHEMA_VERSION:
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def create(self, title: str = DEFAULT_TITLE) -> ConversationSummary:
        now = _now()
        conv = ConversationSummary(uuid.uuid4().hex, title, now, now)
        with self._db() as db:
            db.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?,?,?,?)",
                (conv.id, conv.title, now, now),
            )
        return conv

    def list(self) -> list[ConversationSummary]:
        with self._db() as db:
            rows = db.execute(
                "SELECT id, title, created_at, updated_at FROM conversations "
                "ORDER BY updated_at DESC, rowid DESC"
            ).fetchall()
        return [ConversationSummary(*r) for r in rows]

    def get(self, conversation_id: str) -> Conversation:
        with self._db() as db:
            row = db.execute(
                "SELECT id, title, created_at, updated_at, active_skills FROM conversations "
                "WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                raise UnknownConversation(conversation_id)
            bodies = db.execute(
                "SELECT body FROM messages WHERE conversation_id = ? ORDER BY seq",
                (conversation_id,),
            ).fetchall()
        messages = [Message.model_validate_json(b[0]) for b in bodies]
        return Conversation(*row[:4], messages=messages, active_skills=json.loads(row[4]))

    def append(self, conversation_id: str, messages: list[Message]) -> None:
        """Add messages at the end; an untitled conversation is titled from its first user text."""
        if not messages:
            return
        with self._db() as db:
            row = db.execute(
                "SELECT title FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if row is None:
                raise UnknownConversation(conversation_id)
            last = db.execute(
                "SELECT COALESCE(MAX(seq), -1) FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            db.executemany(
                "INSERT INTO messages (conversation_id, seq, body) VALUES (?,?,?)",
                [
                    (conversation_id, last + 1 + i, m.model_dump_json())
                    for i, m in enumerate(messages)
                ],
            )
            title = row[0]
            if title == DEFAULT_TITLE:
                first_user = next((m.content for m in messages if m.role == "user"), "")
                title = title_from(first_user) if first_user.strip() else title
            db.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, _now(), conversation_id),
            )

    def set_active_skills(self, conversation_id: str, skills: list[str]) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE conversations SET active_skills = ? WHERE id = ?",
                (json.dumps(sorted(set(skills))), conversation_id),
            )

    def rename(self, conversation_id: str, title: str) -> None:
        title = title_from(title)
        with self._db() as db:
            cur = db.execute(
                "UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id)
            )
            if cur.rowcount == 0:
                raise UnknownConversation(conversation_id)

    def delete(self, conversation_id: str) -> None:
        with self._db() as db:
            cur = db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            if cur.rowcount == 0:
                raise UnknownConversation(conversation_id)
