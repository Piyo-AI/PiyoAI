"""The task log: what each agent run did, step by step.

A `RunLog` collects steps while the agent runs (model turns, tool calls, approvals); `RunStore` saves
it once, when the run ends however it ends. Everything is redacted before it is stored: secrets never
reach the database, so they cannot leak from a history view or a bug report.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from piyo.config.model_prices import Price
from piyo.store.conversations import ConversationStore

REDACTED = "[redacted]"
MAX_STRING = 4000  # longer values are cut; the conversation itself keeps the full tool output
MAX_RUNS = 500  # oldest runs are pruned beyond this
MIN_SECRET = 8  # shorter known secrets would redact ordinary words

_SENSITIVE_KEY = re.compile(r"pass(word|wd)|secret|token|api[_-]?key|authorization|credential", re.I)
_PATTERNS = [
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/\-]{16,}=*", re.I),
    re.compile(r"\b(?:ghp|gho|ghs|github_pat|xox[abprs]|AIza)[A-Za-z0-9_\-]{16,}"),
]
_ASSIGNMENT = re.compile(
    r"(?i)\b(pass(?:word|wd)|secret|token|api[_-]?key|authorization)"
    r"(\"?\s*[:=]\s*\"?)([^\s\"',;]{4,})"
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def redact(value, secrets: tuple[str, ...] = ()):
    """Copy of `value` (str, dict, list, anything JSON-like) with secrets masked and long text cut."""
    if isinstance(value, str):
        for known in secrets:
            value = value.replace(known, REDACTED)
        for pattern in _PATTERNS:
            value = pattern.sub(REDACTED, value)
        value = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", value)
        if len(value) > MAX_STRING:
            value = f"{value[:MAX_STRING]}\n[cut, {len(value)} characters total]"
        return value
    if isinstance(value, dict):
        return {
            k: REDACTED
            if isinstance(k, str) and _SENSITIVE_KEY.search(k) and v
            else redact(v, secrets)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v, secrets) for v in value]
    return value


@dataclass
class RunLog:
    """Filled in by the agent (and the approval hook) during one run."""

    conversation_id: str
    provider: str
    model: str
    request: str
    secrets: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(default_factory=_now)
    steps: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_estimated: bool = False
    price: Price | None = None  # for the cost; None when the model's price is unknown
    _t0: float = field(default_factory=time.monotonic, repr=False)

    def __post_init__(self) -> None:
        self.secrets = tuple(s for s in self.secrets if s and len(s) >= MIN_SECRET)
        self.request = redact(self.request, self.secrets)[:200]

    def step(self, kind: str, duration_ms: int | None = None, **data) -> None:
        self.steps.append(
            {
                "kind": kind,
                "at": _now(),
                "duration_ms": duration_ms,
                "data": redact(data, self.secrets),
            }
        )

    def add_tokens(self, input_tokens: int, output_tokens: int, estimated: bool) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.tokens_estimated = self.tokens_estimated or estimated

    @property
    def cost_usd(self) -> float | None:
        return None if self.price is None else self.price.cost(self.input_tokens, self.output_tokens)

    @property
    def duration_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)


@dataclass
class RunSummary:
    id: str
    conversation_id: str
    started_at: str
    provider: str
    model: str
    request: str
    outcome: str  # done | step_limit | truncated | cancelled | error
    error: str | None
    duration_ms: int
    input_tokens: int
    output_tokens: int
    tokens_estimated: bool
    cost_usd: float | None  # None when the price is unknown; 0 for local models


@dataclass
class Run(RunSummary):
    steps: list[dict]


class UnknownRun(KeyError):
    pass


_COLUMNS = (
    "id, conversation_id, started_at, provider, model, request, outcome, error, duration_ms, "
    "input_tokens, output_tokens, tokens_estimated, cost_usd"
)


def _summary(row) -> RunSummary:
    return RunSummary(*row[:11], tokens_estimated=bool(row[11]), cost_usd=row[12])


class RunStore:
    def __init__(self, conversations: ConversationStore) -> None:
        self._conversations = conversations

    def save(self, log: RunLog, outcome: str, error: str | None = None) -> None:
        error = redact(error, log.secrets) if error else None
        with self._conversations.db() as db:
            exists = db.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (log.conversation_id,)
            ).fetchone()
            if not exists:  # deleted while the run was going; nothing to attach the log to
                return
            db.execute(
                f"INSERT INTO runs ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    log.id,
                    log.conversation_id,
                    log.started_at,
                    log.provider,
                    log.model,
                    log.request,
                    outcome,
                    error,
                    log.duration_ms,
                    log.input_tokens,
                    log.output_tokens,
                    int(log.tokens_estimated),
                    log.cost_usd,
                ),
            )
            db.executemany(
                "INSERT INTO run_steps (run_id, seq, body) VALUES (?,?,?)",
                [(log.id, i, json.dumps(s)) for i, s in enumerate(log.steps)],
            )
            db.execute(
                "DELETE FROM runs WHERE id IN (SELECT id FROM runs "
                "ORDER BY started_at DESC, rowid DESC LIMIT -1 OFFSET ?)",
                (MAX_RUNS,),
            )

    def list(self, conversation_id: str | None = None, limit: int = 100) -> list[RunSummary]:
        where, args = "", []
        if conversation_id:
            where, args = "WHERE conversation_id = ?", [conversation_id]
        with self._conversations.db() as db:
            rows = db.execute(
                f"SELECT {_COLUMNS} FROM runs {where} ORDER BY started_at DESC, rowid DESC LIMIT ?",
                [*args, limit],
            ).fetchall()
        return [_summary(r) for r in rows]

    def get(self, run_id: str) -> Run:
        with self._conversations.db() as db:
            row = db.execute(f"SELECT {_COLUMNS} FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise UnknownRun(run_id)
            bodies = db.execute(
                "SELECT body FROM run_steps WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
        return Run(**vars(_summary(row)), steps=[json.loads(b[0]) for b in bodies])
