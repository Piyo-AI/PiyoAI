"""Scheduled jobs, held actions and notifications, in SQLite (`scheduler.db` in the data dir)."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import closing
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

from piyo.config import data_dir

MAX_JOBS = 100
MAX_EVENTS = 200


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    title: str
    prompt: str
    rule: dict
    provider: str
    model: str
    enabled: bool
    next_run: str | None  # ISO UTC; None when finished (a one-time job that ran) or disabled
    last_run: str | None
    last_status: str | None  # done | error | waiting | missed
    last_conversation_id: str | None
    created_at: str


@dataclass
class Pending:
    """An action a scheduled run wanted to take but needs the user for. Nothing happened yet."""

    id: str
    job_id: str
    job_title: str
    conversation_id: str | None
    tool: str
    arguments: dict
    summary: str
    status: str  # pending | approved | declined | expired | failed
    result: str | None
    created_at: str


@dataclass
class Event:
    id: str
    kind: str  # finished | failed | approval | missed
    title: str
    body: str
    job_id: str | None
    conversation_id: str | None
    created_at: str
    read: bool


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL, rule TEXT NOT NULL,
    provider TEXT NOT NULL, model TEXT NOT NULL, enabled INTEGER NOT NULL, next_run TEXT,
    last_run TEXT, last_status TEXT, last_conversation_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, job_title TEXT NOT NULL, conversation_id TEXT,
    tool TEXT NOT NULL, arguments TEXT NOT NULL, summary TEXT NOT NULL, status TEXT NOT NULL,
    result TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
    job_id TEXT, conversation_id TEXT, created_at TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0
);
"""


class SchedulerStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._ready = False

    @property
    def path(self) -> Path:
        return self._path or data_dir() / "scheduler.db"

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        if not self._ready:
            db.executescript(SCHEMA)
            self._ready = True
        return db

    # -- jobs ------------------------------------------------------------------------------

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        d = dict(row)
        return Job(**{**d, "rule": json.loads(d["rule"]), "enabled": bool(d["enabled"])})

    def add_job(self, title: str, prompt: str, rule: dict, provider: str, model: str, next_run: str | None) -> Job:
        with self._lock, closing(self._connect()) as db, db:
            if db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= MAX_JOBS:
                raise ValueError(f"Too many scheduled jobs (limit {MAX_JOBS}). Delete some first.")
            job = Job(uuid.uuid4().hex[:12], title, prompt, rule, provider, model, True, next_run, None, None, None, _now())
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (job.id, title, prompt, json.dumps(rule), provider, model, 1, next_run, None, None, None, job.created_at),
            )
            return job

    def get_job(self, job_id: str) -> Job:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._job(row)

    def list_jobs(self) -> list[Job]:
        with closing(self._connect()) as db:
            return [self._job(r) for r in db.execute("SELECT * FROM jobs ORDER BY created_at, id")]

    def due_jobs(self, now: datetime) -> list[Job]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ? ORDER BY next_run",
                (now.astimezone(UTC).isoformat(timespec="seconds"),),
            )
            return [self._job(r) for r in rows]

    def update_job(self, job_id: str, **changes) -> Job:
        allowed = {f.name for f in fields(Job)} - {"id", "created_at"}
        if bad := set(changes) - allowed:
            raise ValueError(f"cannot change {sorted(bad)}")
        values = {k: (json.dumps(v) if k == "rule" else int(v) if k == "enabled" else v) for k, v in changes.items()}
        with self._lock, closing(self._connect()) as db, db:
            if values:
                sets = ", ".join(f"{k} = ?" for k in values)
                db.execute(f"UPDATE jobs SET {sets} WHERE id = ?", [*values.values(), job_id])
        return self.get_job(job_id)

    def delete_job(self, job_id: str) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            db.execute("UPDATE pending SET status = 'expired' WHERE job_id = ? AND status = 'pending'", (job_id,))

    # -- held actions ----------------------------------------------------------------------

    @staticmethod
    def _pending(row: sqlite3.Row) -> Pending:
        d = dict(row)
        return Pending(**{**d, "arguments": json.loads(d["arguments"])})

    def add_pending(self, job: Job, tool: str, arguments: dict, summary: str) -> Pending:
        item = Pending(uuid.uuid4().hex[:12], job.id, job.title, None, tool, arguments, summary, "pending", None, _now())
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO pending VALUES (?,?,?,?,?,?,?,?,?,?)",
                (item.id, job.id, job.title, None, tool, json.dumps(arguments), summary, "pending", None, item.created_at),
            )
        return item

    def get_pending(self, pending_id: str) -> Pending:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM pending WHERE id = ?", (pending_id,)).fetchone()
        if row is None:
            raise KeyError(pending_id)
        return self._pending(row)

    def list_pending(self, only_open: bool = True) -> list[Pending]:
        sql = "SELECT * FROM pending" + (" WHERE status = 'pending'" if only_open else "")
        with closing(self._connect()) as db:
            return [self._pending(r) for r in db.execute(sql + " ORDER BY created_at DESC LIMIT 200")]

    def set_pending(self, pending_id: str, status: str, result: str | None = None, conversation_id: str | None = None) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "UPDATE pending SET status = ?, result = COALESCE(?, result), "
                "conversation_id = COALESCE(?, conversation_id) WHERE id = ?",
                (status, result, conversation_id, pending_id),
            )

    # -- notifications ---------------------------------------------------------------------

    def add_event(self, kind: str, title: str, body: str, job_id: str | None = None, conversation_id: str | None = None) -> Event:
        event = Event(uuid.uuid4().hex[:12], kind, title, body[:300], job_id, conversation_id, _now(), False)
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,0)",
                (event.id, kind, title, event.body, job_id, conversation_id, event.created_at),
            )
            db.execute(
                "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY created_at DESC, id LIMIT ?)",
                (MAX_EVENTS,),
            )
        return event

    def list_events(self, unread_only: bool = False) -> list[Event]:
        sql = "SELECT * FROM events" + (" WHERE read = 0" if unread_only else "")
        with closing(self._connect()) as db:
            rows = db.execute(sql + " ORDER BY created_at DESC, id LIMIT 100")
            return [Event(**{**dict(r), "read": bool(r["read"])}) for r in rows]

    def mark_read(self, ids: list[str] | None = None) -> None:
        with self._lock, closing(self._connect()) as db, db:
            if ids is None:
                db.execute("UPDATE events SET read = 1")
            else:
                db.executemany("UPDATE events SET read = 1 WHERE id = ?", [(i,) for i in ids])
