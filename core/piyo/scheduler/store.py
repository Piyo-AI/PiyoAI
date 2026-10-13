"""Scheduled jobs, held actions and notifications, in SQLite (`scheduler.db` in the data dir)."""

from __future__ import annotations

import json
import re
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


def folder_name(title: str, job_id: str) -> str:
    """A readable, safe folder name that stays the same when the title changes later."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    return f"{slug or 'agent'}-{job_id[:6]}"


@dataclass
class Job:
    """A background agent: what to do, with which model, when (`rule`; None = only when started by hand), and
    within which limits (`scope`, `priority`, `budget`). Rows from before agents existed have no scope and
    keep what scheduled runs could always do (`piyo.safety.scope.LEGACY`)."""

    id: str
    title: str
    prompt: str
    rule: dict | None
    provider: str
    model: str
    enabled: bool
    next_run: str | None  # ISO UTC; None when finished (a one-time job that ran), disabled or manual
    last_run: str | None
    last_status: str | None  # done | error | waiting | missed | paused | cancelled
    last_conversation_id: str | None
    created_at: str
    scope: dict | None = None  # `Scope.to_json()`; None = legacy
    priority: str = "normal"  # low | normal | high
    budget: dict | None = None  # max_steps / max_tokens / timeout_s overriding the global run settings
    folder: str | None = None  # the agent's own folder name under <workspace>/agents/


@dataclass
class AgentRun:
    """One run of an agent, persisted so a restart can pick it up again."""

    id: str
    job_id: str
    state: str  # queued | running | waiting | paused | done | needs-you | failed | cancelled
    reason: str | None  # why it is waiting or paused, or the error
    conversation_id: str | None
    held: int  # actions held for the user's approval
    created_at: str
    updated_at: str


ACTIVE_STATES = ("queued", "running", "waiting", "paused")


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
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, state TEXT NOT NULL, reason TEXT, conversation_id TEXT,
    held INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""
# Agent columns added to `jobs` after the first release (added on open when missing).
JOB_COLUMNS = {
    "scope": "TEXT",
    "priority": "TEXT NOT NULL DEFAULT 'normal'",
    "budget": "TEXT",
    "folder": "TEXT",
}
MAX_RUNS_KEPT = 500


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
            with db:
                db.executescript(SCHEMA)
                have = {r["name"] for r in db.execute("PRAGMA table_info(jobs)")}
                for name, kind in JOB_COLUMNS.items():
                    if name not in have:
                        db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {kind}")
            self._ready = True
        return db

    # -- jobs ------------------------------------------------------------------------------

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        d = dict(row)
        decoded = {k: json.loads(d[k]) if d[k] is not None else None for k in ("rule", "scope", "budget")}
        return Job(**{**d, **decoded, "enabled": bool(d["enabled"])})

    def add_job(
        self,
        title: str,
        prompt: str,
        rule: dict | None,
        provider: str,
        model: str,
        next_run: str | None,
        scope: dict | None = None,
        priority: str = "normal",
        budget: dict | None = None,
    ) -> Job:
        with self._lock, closing(self._connect()) as db, db:
            if db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= MAX_JOBS:
                raise ValueError(f"Too many agents and scheduled jobs (limit {MAX_JOBS}). Delete some first.")
            job_id = uuid.uuid4().hex[:12]
            job = Job(
                job_id, title, prompt, rule, provider, model, True, next_run, None, None, None, _now(),
                scope, priority, budget, folder_name(title, job_id),
            )
            values = {
                **vars(job),
                "rule": json.dumps(rule),  # "null" for a manual agent (the column is NOT NULL)
                "scope": json.dumps(scope) if scope is not None else None,
                "budget": json.dumps(budget) if budget is not None else None,
                "enabled": 1,
            }
            db.execute(
                f"INSERT INTO jobs ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                list(values.values()),
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
                "SELECT * FROM jobs WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ? "
                "ORDER BY next_run",
                (now.astimezone(UTC).isoformat(timespec="seconds"),),
            )
            return [self._job(r) for r in rows]

    def update_job(self, job_id: str, **changes) -> Job:
        allowed = {f.name for f in fields(Job)} - {"id", "created_at"}
        if bad := set(changes) - allowed:
            raise ValueError(f"cannot change {sorted(bad)}")
        def column(k: str, v):
            if k == "rule":
                return json.dumps(v)
            if k in ("scope", "budget"):
                return json.dumps(v) if v is not None else None
            return int(v) if k == "enabled" else v

        values = {k: column(k, v) for k, v in changes.items()}
        with self._lock, closing(self._connect()) as db, db:
            if values:
                sets = ", ".join(f"{k} = ?" for k in values)
                db.execute(f"UPDATE jobs SET {sets} WHERE id = ?", [*values.values(), job_id])
        return self.get_job(job_id)

    def delete_job(self, job_id: str) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            db.execute(
                "UPDATE pending SET status = 'expired' WHERE job_id = ? AND status = 'pending'", (job_id,)
            )

    # -- agent runs ------------------------------------------------------------------------

    def add_run(self, job_id: str, state: str = "queued") -> AgentRun:
        stamp = _now()
        run = AgentRun(uuid.uuid4().hex[:12], job_id, state, None, None, 0, stamp, stamp)
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,?)", tuple(vars(run).values()))
            db.execute(
                "DELETE FROM agent_runs WHERE id NOT IN "
                "(SELECT id FROM agent_runs ORDER BY created_at DESC, id LIMIT ?)",
                (MAX_RUNS_KEPT,),
            )
        return run

    def get_run(self, run_id: str) -> AgentRun:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return AgentRun(**dict(row))

    def update_run(self, run_id: str, **changes) -> AgentRun:
        allowed = {"state", "reason", "conversation_id", "held"}
        if bad := set(changes) - allowed:
            raise ValueError(f"cannot change {sorted(bad)}")
        changes["updated_at"] = _now()
        with self._lock, closing(self._connect()) as db, db:
            sets = ", ".join(f"{k} = ?" for k in changes)
            db.execute(f"UPDATE agent_runs SET {sets} WHERE id = ?", [*changes.values(), run_id])
        return self.get_run(run_id)

    def list_runs(
        self, job_id: str | None = None, active_only: bool = False, limit: int = 50
    ) -> list[AgentRun]:
        where, params = [], []
        if job_id is not None:
            where.append("job_id = ?")
            params.append(job_id)
        if active_only:
            where.append(f"state IN ({', '.join('?' * len(ACTIVE_STATES))})")
            params.extend(ACTIVE_STATES)
        sql = "SELECT * FROM agent_runs" + (f" WHERE {' AND '.join(where)}" if where else "")
        with closing(self._connect()) as db:
            rows = db.execute(sql + " ORDER BY created_at DESC, id LIMIT ?", [*params, limit])
            return [AgentRun(**dict(r)) for r in rows]

    # -- held actions ----------------------------------------------------------------------

    @staticmethod
    def _pending(row: sqlite3.Row) -> Pending:
        d = dict(row)
        return Pending(**{**d, "arguments": json.loads(d["arguments"])})

    def add_pending(
        self, job: Job, tool: str, arguments: dict, summary: str, created_at: datetime | None = None
    ) -> Pending:
        """`created_at` is the scheduler's own clock, which is what expiry is measured against."""
        stamp = created_at.astimezone(UTC).isoformat(timespec="seconds") if created_at else _now()
        item = Pending(
            uuid.uuid4().hex[:12], job.id, job.title, None, tool, arguments, summary, "pending", None, stamp
        )
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO pending VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    item.id,
                    job.id,
                    job.title,
                    None,
                    tool,
                    json.dumps(arguments),
                    summary,
                    "pending",
                    None,
                    item.created_at,
                ),
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

    def set_pending(
        self, pending_id: str, status: str, result: str | None = None, conversation_id: str | None = None
    ) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "UPDATE pending SET status = ?, result = COALESCE(?, result), "
                "conversation_id = COALESCE(?, conversation_id) WHERE id = ?",
                (status, result, conversation_id, pending_id),
            )

    # -- notifications ---------------------------------------------------------------------

    def add_event(
        self, kind: str, title: str, body: str, job_id: str | None = None, conversation_id: str | None = None
    ) -> Event:
        event = Event(uuid.uuid4().hex[:12], kind, title, body[:300], job_id, conversation_id, _now(), False)
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,0)",
                (event.id, kind, title, event.body, job_id, conversation_id, event.created_at),
            )
            db.execute(
                "DELETE FROM events WHERE id NOT IN "
                "(SELECT id FROM events ORDER BY created_at DESC, id LIMIT ?)",
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
