"""What Piyo remembers about the user: short facts in SQLite (`memory.db` in the data dir).

Everything here is visible and editable in the app. Memory is data, never instructions: it is fenced when it
reaches the model. Secrets and ID-like numbers are refused at the door, and sensitive categories stay off
until the user turns them on.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from piyo.config import data_dir
from piyo.store.embeddings import EmbeddingError, SemanticIndex, doc_key, document_text
from piyo.store.runs import redact

CATEGORIES = ("preference", "person", "routine", "account", "outcome", "note")
SENSITIVE_CATEGORIES = ("health", "finance", "identity")
MAX_TEXT = 500
MAX_ITEMS = 1000
MAX_DISTANCE = 0.30  # cosine distance; related short sentences measured 0.1-0.25, unrelated 0.35 and up
RRF_K = 60  # reciprocal rank fusion: how fast a hit's weight falls with its rank
INDEX_BATCH = 16
# Filler words say nothing about which memory is meant ("is" would match every "X is called Y").
STOPWORDS = frozenset(
    "a an the is are was were be am do does did my me we our you your it its this that and or of to in on at "
    "for with about what which who whom where when how".split()
)

log = logging.getLogger(__name__)

_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_ID_NUMBER = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CREDENTIAL = re.compile(r"(?i)\b(pass(?:word|code|wd)|pin|cvv|otp)\b\s*(?:is|was|:|=)")


class MemoryRefused(ValueError):
    """Why something can't be remembered; the message goes back to the model and the user."""


@dataclass
class Memory:
    id: str
    category: str
    text: str
    source: str  # "user" (typed in the app) or "piyo" (remembered during a chat)
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def check_text(text: object) -> str:
    if not isinstance(text, str) or not text.strip():
        raise MemoryRefused("There is nothing to remember: the text is empty.")
    text = " ".join(text.split())
    if len(text) > MAX_TEXT:
        raise MemoryRefused(f"Too long to remember ({len(text)} characters, limit {MAX_TEXT}). Shorten it.")
    if _CARD.search(text) or _ID_NUMBER.search(text) or _CREDENTIAL.search(text) or redact(text) != text:
        raise MemoryRefused(
            "That looks like a password, key, card or ID number. Piyo never stores those; "
            "the user has to enter them themselves each time."
        )
    return text


class MemorySettings:
    """User switches for memory. Sensitive categories are off until turned on."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or data_dir() / "memory_settings.json"

    def _read(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def sensitive(self) -> bool:
        return self._read().get("sensitive") is True

    def set_sensitive(self, on: bool) -> None:
        self.path.write_text(json.dumps({**self._read(), "sensitive": bool(on)}), encoding="utf-8")

    def semantic(self) -> bool:
        return self._read().get("semantic") is True

    def set_semantic(self, on: bool) -> None:
        self.path.write_text(json.dumps({**self._read(), "semantic": bool(on)}), encoding="utf-8")


class MemoryStore:
    def __init__(
        self,
        path: Path | None = None,
        settings: MemorySettings | None = None,
        semantic: SemanticIndex | None = None,
        background_index: bool = True,
    ) -> None:
        self._path = path
        self.settings = settings or MemorySettings()
        self.semantic = semantic
        self._background_index = background_index
        self._lock = threading.Lock()
        self._index_lock = threading.Lock()
        self._indexing = False
        self._index_again = False
        self._ready = False

    @property
    def path(self) -> Path:
        return self._path or data_dir() / "memory.db"

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        if not self._ready:
            db.execute(
                "CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, category TEXT NOT NULL, "
                "text TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS memory_vec_state (id TEXT PRIMARY KEY, doc TEXT NOT NULL)"
            )
            self._ready = True
        return db

    def allowed_categories(self) -> tuple[str, ...]:
        return CATEGORIES + (SENSITIVE_CATEGORIES if self.settings.sensitive() else ())

    def _check_category(self, category: object) -> str:
        if category in self.allowed_categories():
            return str(category)
        if category in SENSITIVE_CATEGORIES:
            raise MemoryRefused(
                f"The {category} category is switched off. The user can allow it in Settings > Memory; "
                "until then do not store it."
            )
        raise MemoryRefused(
            f"Unknown category {category!r}. Use one of: {', '.join(self.allowed_categories())}."
        )

    # -- writes ----------------------------------------------------------------------------

    def add(self, text: object, category: object = "note", source: str = "piyo") -> Memory:
        text, category = check_text(text), self._check_category(category)
        with self._lock, closing(self._connect()) as db, db:
            same = db.execute(
                "SELECT * FROM memories WHERE category = ? AND lower(text) = lower(?)", (category, text)
            ).fetchone()
            if same:
                db.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (_now(), same["id"]))
                found = self._get(db, same["id"])
                self.reindex_later()
                return found
            if db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] >= MAX_ITEMS:
                raise MemoryRefused(f"Memory is full ({MAX_ITEMS} items). Ask the user to delete some.")
            now = _now()
            item = Memory(uuid.uuid4().hex[:12], category, text, source, now, now)
            db.execute("INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?)", tuple(asdict(item).values()))
            self.reindex_later()
            return item

    def update(self, memory_id: str, text: object = None, category: object = None) -> Memory:
        with self._lock, closing(self._connect()) as db, db:
            current = self._get(db, memory_id)
            new_text = check_text(text) if text is not None else current.text
            new_cat = self._check_category(category) if category is not None else current.category
            db.execute(
                "UPDATE memories SET text = ?, category = ?, updated_at = ? WHERE id = ?",
                (new_text, new_cat, _now(), memory_id),
            )
            self.reindex_later()
            return self._get(db, memory_id)

    def delete(self, memory_id: str) -> None:
        with self._lock, closing(self._connect()) as db, db:
            self._get(db, memory_id)
            db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))

    def clear(self) -> int:
        with self._lock, closing(self._connect()) as db, db:
            return db.execute("DELETE FROM memories").rowcount

    # -- reads -----------------------------------------------------------------------------

    @staticmethod
    def _get(db: sqlite3.Connection, memory_id: str) -> Memory:
        row = db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return Memory(**dict(row))

    def get(self, memory_id: str) -> Memory:
        with closing(self._connect()) as db:
            return self._get(db, memory_id)

    def list(self, category: str | None = None, limit: int = 200, offset: int = 0) -> list[Memory]:
        sql, args = "SELECT * FROM memories", []
        if category:
            sql += " WHERE category = ?"
            args.append(category)
        sql += " ORDER BY updated_at DESC, id LIMIT ? OFFSET ?"
        with closing(self._connect()) as db:
            return [Memory(**dict(r)) for r in db.execute(sql, [*args, limit, offset])]

    def search(self, query: str, category: str | None = None, limit: int = 10) -> list[Memory]:
        """Items matching the query: by shared words, and by meaning when semantic search is on.

        Blocking (it may embed the query), so async callers run it in a thread. Without the embedding model
        (off, not downloaded, or failing) this is the keyword search alone.
        """
        words = {w for w in re.findall(r"\w+", query.lower()) if len(w) > 1 and w not in STOPWORDS}
        items = self.list(category, limit=MAX_ITEMS)
        if not words:
            return items[:limit]
        keyword = self._keyword_hits(words, items)
        meaning = self._semantic_hits(query, category, limit)
        if not meaning:
            return keyword[:limit]
        score: dict[str, float] = {}
        for ranked in (keyword, meaning):
            for rank, item in enumerate(ranked):
                score[item.id] = score.get(item.id, 0.0) + 1 / (RRF_K + rank)
        by_id = {m.id: m for m in (*keyword, *meaning)}
        order = sorted(score, key=lambda i: -score[i])  # stable: ties keep first-seen order
        return [by_id[i] for i in order[:limit]]

    @staticmethod
    def _keyword_hits(words: set[str], items: list[Memory]) -> list[Memory]:
        """Items sharing the most words with the query come first, newest breaking ties."""
        scored = []
        for item in items:
            have = set(re.findall(r"\w+", f"{item.text} {item.category}".lower()))
            hits = sum(1 for w in words if w in have or any(h.startswith(w) for h in have))
            if hits:
                scored.append((hits, item))
        scored.sort(key=lambda p: -p[0])  # stable: equal scores keep the newest first
        return [item for _, item in scored]

    def _semantic_hits(self, query: str, category: str | None, limit: int) -> list[Memory]:
        sem = self.semantic
        if sem is None or not sem.usable():
            return []
        try:
            vector = sem.embedder.embed([query], query=True)[0]
            with closing(self._connect()) as db, db:
                if not sem.attach(db):
                    return []
                near = sem.nearest(db, vector, min(MAX_ITEMS, max(limit * 4, 20)))
                fresh = sem.fresh(db)
                found = []
                for memory_id, distance in near:
                    if distance > MAX_DISTANCE or memory_id not in fresh:
                        continue
                    item = self._get(db, memory_id)
                    if category is None or item.category == category:
                        found.append(item)
                return found
        except (EmbeddingError, sqlite3.Error, KeyError) as e:
            sem.last_error = str(e)
            log.warning("semantic memory search failed, using keywords only: %s", e)
            return []

    # -- the vector index ------------------------------------------------------------------

    def reindex_later(self) -> None:
        """Embed what is new or changed in a background thread (one at a time; a write never waits)."""
        sem = self.semantic
        if sem is None or not self._background_index or not sem.usable():
            return
        with self._index_lock:
            if self._indexing:
                self._index_again = True
                return
            self._indexing = True
        threading.Thread(target=self._index_worker, daemon=True, name="memory-index").start()

    def _index_worker(self) -> None:
        try:
            while True:
                self.index_pending()
                with self._index_lock:
                    if not self._index_again:
                        self._indexing = False
                        return
                    self._index_again = False
        except Exception:
            with self._index_lock:
                self._indexing = False
            log.exception("memory indexing failed")

    def index_pending(self) -> int:
        """Embed every memory without a current vector and return how many were done. Blocking."""
        sem = self.semantic
        if sem is None or not sem.usable():
            return 0
        with self._lock, closing(self._connect()) as db, db:
            if not sem.attach(db):
                return 0
            sem.prune(db)
            todo = sem.pending(db)
        done = 0
        for start in range(0, len(todo), INDEX_BATCH):
            chunk = todo[start : start + INDEX_BATCH]
            try:
                vectors = sem.embedder.embed([document_text(c, t) for _, c, t in chunk])
            except EmbeddingError as e:
                sem.last_error = str(e)
                log.warning("memory indexing stopped: %s", e)
                break
            with self._lock, closing(self._connect()) as db, db:
                if not sem.attach(db):
                    break
                for (memory_id, category, text), vector in zip(chunk, vectors, strict=True):
                    row = db.execute("SELECT category, text FROM memories WHERE id = ?", (memory_id,))
                    now = row.fetchone()
                    if now and (now[0], now[1]) == (category, text):  # else edited or deleted meanwhile: skip
                        sem.put(db, memory_id, doc_key(category, text), vector)
                        done += 1
            sem.last_error = ""
        return done

    def semantic_counts(self) -> tuple[int, int]:
        """(memories with a current vector, all memories); the first is 0 while the index is not usable."""
        with closing(self._connect()) as db, db:
            total = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            sem = self.semantic
            if sem is None or not sem.usable() or not sem.attach(db):
                return 0, total
            return sem.counts(db)

    def drop_vectors(self) -> None:
        """Forget every vector (semantic search removed); the text is untouched."""
        sem = self.semantic
        with self._lock, closing(self._connect()) as db, db:
            db.execute("DELETE FROM memory_vec_state")
            if sem is not None and sem.attach(db):
                db.execute("DROP TABLE IF EXISTS memory_vec")
                db.execute("DROP TABLE IF EXISTS memory_vec_meta")

    def export(self) -> list[dict]:
        return [asdict(m) for m in self.list(limit=MAX_ITEMS)]
