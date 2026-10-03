"""Audit record of every approval decision (allowed or declined), kept in a hash chain.

Each entry's hash covers its content and the previous entry's hash, so editing or deleting an old
entry breaks every hash after it. A small head file next to the database also remembers the newest
hash and the entry count, which catches entries cut off the end. This shows tampering by mistake or
by a casual edit; someone with full control of the data folder could rewrite both files.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from piyo.store.conversations import ConversationStore
from piyo.store.runs import redact

GENESIS = "0" * 64
HEAD_FILE = "audit_head.json"


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class AuditEntry:
    seq: int
    at: str
    run_id: str
    conversation_id: str
    tool: str
    summary: str
    arguments: dict  # secrets masked and long text cut, like the task log
    args_digest: str  # SHA-256 of the exact arguments the user decided on
    why: str
    decision: str  # "allowed" | "declined"
    prev_hash: str
    hash: str


@dataclass
class AuditReport:
    entries: list[AuditEntry]
    verified: bool
    problem: str | None = None


def _entry_hash(prev: str, fields: dict) -> str:
    return hashlib.sha256((prev + "\n" + json.dumps(fields, sort_keys=True)).encode()).hexdigest()


class AuditStore:
    def __init__(self, conversations: ConversationStore) -> None:
        self._conversations = conversations

    @property
    def _head_path(self):
        return self._conversations.path.with_name(HEAD_FILE)

    def record(
        self,
        run_id: str,
        conversation_id: str,
        tool: str,
        summary: str,
        arguments: dict,
        why: str,
        allowed: bool,
        secrets: tuple[str, ...] = (),
    ) -> None:
        fields = {
            "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "run_id": run_id,
            "conversation_id": conversation_id,
            "tool": tool,
            "summary": redact(summary, secrets),
            "arguments": redact(arguments, secrets),
            "args_digest": _digest(arguments),
            "why": redact(why, secrets),
            "decision": "allowed" if allowed else "declined",
        }
        with self._conversations.db() as db:
            row = db.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            seq, prev = (row[0] + 1, row[1]) if row else (1, GENESIS)
            digest = _entry_hash(prev, fields)
            db.execute(
                "INSERT INTO audit (seq, prev_hash, hash, body) VALUES (?,?,?,?)",
                (seq, prev, digest, json.dumps(fields, sort_keys=True)),
            )
        self._head_path.write_text(json.dumps({"seq": seq, "hash": digest}), encoding="utf-8")

    def report(self, limit: int = 200, offset: int = 0) -> AuditReport:
        """Entries newest first, skipping the newest `offset`; and whether the chain is intact."""
        with self._conversations.db() as db:
            rows = db.execute("SELECT seq, prev_hash, hash, body FROM audit ORDER BY seq").fetchall()
        problem = self._check(rows)
        newest_first = rows[::-1][offset : offset + limit]
        entries = [
            AuditEntry(seq, **json.loads(body), prev_hash=prev, hash=h)
            for seq, prev, h, body in newest_first
        ]
        return AuditReport(entries, problem is None, problem)

    def _check(self, rows) -> str | None:
        prev = GENESIS
        for n, (seq, prev_hash, h, body) in enumerate(rows, start=1):
            if seq != n:
                return f"Entry {n} is missing."
            if prev_hash != prev or _entry_hash(prev, json.loads(body)) != h:
                return f"Entry {seq} does not match its record."
            prev = h
        try:
            head = json.loads(self._head_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            head = None
        if head is None:
            return None if not rows else "The audit head file is missing."
        if head.get("seq") != len(rows) or head.get("hash") != prev:
            return "Entries were removed from the end of the record."
        return None
