"""Semantic memory search: EmbeddingGemma 2 run by llama.cpp, vectors kept by sqlite-vec.

Nothing here ships in the installer (about 330 MB together). When the user turns on "Find memories by
meaning" the first time, `EmbeddingSetup` downloads three pinned files into `<tools dir>/embeddings/`: the
llama.cpp release (its `llama-server`), the sqlite-vec loadable extension and the model weights. Every file
is checked against the SHA-256 in `embedding_pins.json` before it is used; a mismatch deletes it. Nothing is
looked up at run time (no "latest"); to upgrade, change the version and every hash together.

`LlamaEmbedder` runs `llama-server` on 127.0.0.1 with a random key, only while memory needs vectors, and stops
it after a few idle minutes. Memory text goes to that local process and nowhere else. `SemanticIndex` is the
sqlite-vec side: a `vec0` table inside `memory.db`, which `MemoryStore` keeps in step with the text.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import math
import os
import secrets
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from piyo.config import tools_dir
from piyo.skills.helper_tools import Fetch, HelperError, http_download, platform_key, sha256_file

PINS = Path(__file__).with_name("embedding_pins.json")
DIMS = 256  # EmbeddingGemma gives 768; the first 256 are a trained shorter form (Matryoshka), renormalised
QUERY_PREFIX = "task: search result | query: "
MAX_INPUT_CHARS = 2000
IDLE_SECONDS = 180
START_TIMEOUT = 120


class EmbeddingError(Exception):
    """Why semantic search is not working; the message is for the user."""


def load_pins() -> dict:
    return json.loads(PINS.read_text(encoding="utf-8"))


SAME_DOC = "(m.category || char(31) || m.text)"  # what memory_vec_state.doc holds for a current vector


def doc_key(category: str, text: str) -> str:
    return f"{category}{text}"


def document_text(category: str, text: str) -> str:
    return f"title: {category} | text: {text[:MAX_INPUT_CHARS]}"


def query_text(text: str) -> str:
    return QUERY_PREFIX + text[:MAX_INPUT_CHARS]


def shorten(vector: list[float]) -> list[float]:
    """The first DIMS values, scaled to length 1 (cosine distance and dot product then agree)."""
    head = vector[:DIMS]
    norm = math.sqrt(sum(x * x for x in head)) or 1.0
    return [x / norm for x in head]


def pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


# -- downloading ----------------------------------------------------------------------------------


@dataclass
class SemanticStatus:
    state: str  # missing | installing | ready | failed
    percent: int = 0
    message: str = ""
    approx_mb: int = 0
    model: str = ""


def _vec_library() -> str:
    return "vec0" + {"win32": ".dll", "darwin": ".dylib"}.get(sys.platform, ".so")


def _safe_extract(archive: Path, out: Path) -> None:
    """Unpack a zip or tar.gz into `out`; names that leave the folder (or unsafe links) refuse the archive."""
    root = out.resolve()
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                if not (root / name).resolve().is_relative_to(root):
                    raise HelperError("The download has a file outside its folder, so it was refused.")
            zf.extractall(out)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(out, filter="data")  # "data" refuses traversal, absolute names and escaping links


class EmbeddingSetup:
    """The three downloaded pieces: where they are, whether they are all there, and getting them."""

    def __init__(self, folder: Path | None = None, fetch: Fetch = http_download) -> None:
        self._folder = folder
        self._fetch = fetch
        self._state: SemanticStatus | None = None
        self._task: asyncio.Task | None = None
        self.on_ready: Callable[[], None] | None = None

    @property
    def folder(self) -> Path:
        return self._folder or tools_dir() / "embeddings"

    # -- where things are ------------------------------------------------------------------------

    def _llama_dir(self) -> Path:
        return self.folder / f"llama-{load_pins()['llama']['version']}"

    def server_path(self) -> Path | None:
        name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
        found = next(self._llama_dir().rglob(name), None) if self._llama_dir().is_dir() else None
        return found if found and found.is_file() else None

    def vec_path(self) -> Path | None:
        path = self.folder / f"sqlite-vec-{load_pins()['sqlite-vec']['version']}" / _vec_library()
        return path if path.is_file() else None

    def model_path(self) -> Path | None:
        model = load_pins()["model"]
        path = self.folder / model["id"] / model["file"]
        return path if path.is_file() and path.stat().st_size == model["size"] else None

    def ready(self) -> bool:
        return all((self.server_path(), self.vec_path(), self.model_path()))

    def model_id(self) -> str:
        return load_pins()["model"]["id"]

    def total_mb(self) -> int:
        pins = load_pins()
        return sum(int(pins[k]["approx_mb"]) for k in ("model", "llama", "sqlite-vec"))

    def status(self) -> SemanticStatus:
        if self._state is not None and self._state.state in ("installing", "failed"):
            return self._state
        mb = self.total_mb()
        if self.ready():
            return SemanticStatus("ready", 100, approx_mb=mb, model=load_pins()["model"]["name"])
        return SemanticStatus("missing", approx_mb=mb)

    # -- getting them ----------------------------------------------------------------------------

    def start(self) -> SemanticStatus:
        """Begin the download in the background; no-op when everything is there or it is running."""
        if not self.ready() and (self._task is None or self._task.done()):
            self._state = SemanticStatus("installing", 0, "Starting…", self.total_mb())
            self._task = asyncio.ensure_future(self._install())
        return self.status()

    async def ensure(self) -> None:
        if not self.ready():
            self.start()
            if self._task is not None:
                await asyncio.shield(self._task)
        if not self.ready():
            raise EmbeddingError(self.status().message or "The semantic search files are not downloaded.")

    def remove(self) -> None:
        """Delete everything downloaded (the user's "remove" button)."""
        shutil.rmtree(self.folder, ignore_errors=True)
        self._state = None

    async def _install(self) -> None:
        pins = load_pins()
        key = platform_key() if _supported() else None
        weights = {k: int(pins[k]["approx_mb"]) for k in ("llama", "sqlite-vec", "model")}
        total = sum(weights.values())
        done = 0

        def say(percent: int, message: str) -> None:
            self._state = SemanticStatus("installing", min(99, percent), message, total)

        try:
            if key is None or key not in pins["llama"]["files"] or key not in pins["sqlite-vec"]["files"]:
                raise HelperError(f"Semantic search is not available for {sys.platform} on this processor.")
            self.folder.mkdir(parents=True, exist_ok=True)
            for part in ("sqlite-vec", "llama"):
                entry = pins[part]["files"][key]
                url = pins[part]["url"].format(version=pins[part]["version"], file=entry["file"])
                label = {"sqlite-vec": "the vector search library", "llama": "the model runner"}[part]
                base = done
                await self._get_archive(
                    part,
                    url,
                    entry,
                    lambda r, t, b=base, w=weights[part], lb=label: say(
                        (b + (w * r // t if t else 0)) * 100 // total, f"Downloading {lb}…"
                    ),
                    pins,
                )
                done += weights[part]
            model = pins["model"]
            base = done
            await self._get_model(
                model,
                lambda r, t: say(
                    (base + weights["model"] * r // (t or model["size"])) * 100 // total,
                    f"Downloading {model['name']}…",
                ),
            )
            self._state = None
            if self.ready() and self.on_ready:
                self.on_ready()
        except HelperError as e:
            self._state = SemanticStatus("failed", 0, str(e), total)
        except (httpx.HTTPError, OSError, zipfile.BadZipFile, tarfile.TarError) as e:
            why = (
                f"the server answered {e.response.status_code}"
                if isinstance(e, httpx.HTTPStatusError)
                else type(e).__name__
            )
            self._state = SemanticStatus(
                "failed",
                0,
                f"Could not download: {why}. Check your internet connection and try again.",
                total,
            )

    async def _get_archive(self, part: str, url: str, entry: dict, progress, pins: dict) -> None:
        final = self._llama_dir() if part == "llama" else self.folder / f"sqlite-vec-{pins[part]['version']}"
        if self.server_path() if part == "llama" else self.vec_path():
            return
        archive = self.folder / f".{os.getpid()}-{entry['file']}"
        staged = self.folder / f".{part}-{os.getpid()}"
        try:
            await self._fetch(url, archive, progress)
            if await asyncio.to_thread(sha256_file, archive) != entry["sha256"]:
                raise HelperError(f"The {part} download does not match its fingerprint; it was deleted.")
            shutil.rmtree(staged, ignore_errors=True)
            staged.mkdir(parents=True)
            await asyncio.to_thread(_safe_extract, archive, staged)
            if part == "sqlite-vec":
                lib = next(staged.rglob(_vec_library()), None)
                if lib is None:
                    raise HelperError("The vector search download has no library for this system.")
                shutil.rmtree(final, ignore_errors=True)
                final.mkdir(parents=True)
                shutil.move(str(lib), final / _vec_library())
            else:
                shutil.rmtree(final, ignore_errors=True)
                os.replace(staged, final)
                for program in final.rglob("llama-server*"):
                    program.chmod(program.stat().st_mode | 0o111)
                for old in self.folder.glob("llama-*"):
                    if old != final and old.is_dir():
                        shutil.rmtree(old, ignore_errors=True)
        finally:
            archive.unlink(missing_ok=True)
            shutil.rmtree(staged, ignore_errors=True)

    async def _get_model(self, model: dict, progress) -> None:
        if self.model_path():
            return
        target = self.folder / model["id"] / model["file"]
        staged = target.with_name(f".{os.getpid()}-{model['file']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            await self._fetch(model["url"], staged, progress)
            if await asyncio.to_thread(sha256_file, staged) != model["sha256"]:
                raise HelperError("The model download does not match its fixed fingerprint; it was deleted.")
            os.replace(staged, target)
        finally:
            staged.unlink(missing_ok=True)


def _supported() -> bool:
    try:
        platform_key()
    except HelperError:
        return False
    return True


# -- running the model ----------------------------------------------------------------------------


def _process_name(pid: int) -> str:
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout
            return out.split(",")[0].strip('"').lower() if out.strip().startswith('"') else ""
        return (
            subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, timeout=10)
            .stdout.strip()
            .lower()
        )
    except (OSError, subprocess.SubprocessError):
        return ""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LlamaEmbedder:
    """`llama-server` on loopback, started on first use and stopped when idle. `embed` is blocking."""

    def __init__(self, setup: EmbeddingSetup, idle_seconds: float = IDLE_SECONDS) -> None:
        self._setup = setup
        self._idle = idle_seconds
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._url = ""
        self._key = ""
        self._timer: threading.Timer | None = None
        atexit.register(self.close)

    @property
    def _pid_file(self) -> Path:
        return self._setup.folder / "embedding-server.pid"

    @property
    def log_file(self) -> Path:
        return self._setup.folder / "embedding-server.log"

    def embed(self, texts: list[str], query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        prepared = [query_text(t) if query else t for t in texts]
        with self._lock:
            for attempt in (1, 2):
                self._ensure_running()
                try:
                    res = httpx.post(
                        f"{self._url}/v1/embeddings",
                        json={"input": prepared, "model": "embeddinggemma"},
                        headers={"Authorization": f"Bearer {self._key}"},
                        timeout=120,
                    )
                    res.raise_for_status()
                    rows = sorted(res.json()["data"], key=lambda d: d["index"])
                    break
                except (httpx.HTTPError, KeyError, ValueError) as e:
                    self._stop()  # a dead or stuck server: start a fresh one once
                    if attempt == 2:
                        note = f"{type(e).__name__}; see {self.log_file.name}"
                        raise EmbeddingError(f"The embedding model gave no answer ({note}).") from None
            self._touch()
        if len(rows) != len(texts):
            raise EmbeddingError("The embedding model returned the wrong number of results.")
        return [shorten(r["embedding"]) for r in rows]

    # -- process ---------------------------------------------------------------------------------

    def _ensure_running(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        server, model = self._setup.server_path(), self._setup.model_path()
        if server is None or model is None:
            raise EmbeddingError("The semantic search files are not downloaded yet.")
        self._kill_stale()
        port, self._key = _free_port(), secrets.token_urlsafe(24)
        self._url = f"http://127.0.0.1:{port}"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_file.open("wb")
        try:
            self._proc = subprocess.Popen(
                [
                    str(server),
                    "-m",
                    str(model),
                    "--embeddings",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--api-key",
                    self._key,
                    "-c",
                    "2048",
                    "-np",
                    "1",
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=server.parent,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except OSError as e:
            why = e.strerror or type(e).__name__
            raise EmbeddingError(f"Could not start the embedding model: {why}.") from None
        finally:
            log.close()
        self._pid_file.write_text(str(self._proc.pid), encoding="utf-8")
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                self._stop()
                raise EmbeddingError(f"The embedding model stopped while starting. See {self.log_file.name}.")
            try:
                if httpx.get(f"{self._url}/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        self._stop()
        raise EmbeddingError("The embedding model took too long to start.")

    def _kill_stale(self) -> None:
        """A server left by a core that was killed: stop it, but only if the pid is still a llama-server."""
        try:
            pid = int(self._pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return
        if "llama-server" in _process_name(pid):
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        self._pid_file.unlink(missing_ok=True)

    def _touch(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self._idle, self.close)
        self._timer.daemon = True
        self._timer.start()

    def _stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._pid_file.unlink(missing_ok=True)

    def close(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._stop()


# -- the sqlite-vec side --------------------------------------------------------------------------


class SemanticIndex:
    """The `memory_vec` table in memory.db and the calls that read and write it.

    `memory_vec_state` is an ordinary table that records the category and text each vector was made from. A
    memory that changed since (even through a connection without the extension) no longer matches, so it
    counts as pending and its old vector is never returned.
    """

    def __init__(self, setup, embedder, enabled: Callable[[], bool]) -> None:
        self.setup, self.embedder, self._enabled = setup, embedder, enabled
        self.last_error = ""

    def usable(self) -> bool:
        return self._enabled() and self.setup.ready()

    def attach(self, db: sqlite3.Connection) -> bool:
        """Load the extension and make the tables match the model (the caller commits); False if it can't."""
        path = self.setup.vec_path()
        if path is None:
            return False
        try:
            db.enable_load_extension(True)
            try:
                db.load_extension(str(path))
            finally:
                db.enable_load_extension(False)
        except (AttributeError, sqlite3.Error) as e:
            self.last_error = f"This Python cannot load the vector search library ({type(e).__name__})."
            return False
        db.execute("CREATE TABLE IF NOT EXISTS memory_vec_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        have = db.execute("SELECT value FROM memory_vec_meta WHERE key = 'model'").fetchone()
        if have is None or have[0] != self.setup.model_id():
            # first use, or another model or size: any old vectors mean nothing now
            db.execute("DROP TABLE IF EXISTS memory_vec")
            db.execute("DELETE FROM memory_vec_state")
            db.execute("INSERT OR REPLACE INTO memory_vec_meta VALUES ('model', ?)", (self.setup.model_id(),))
        db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0("
            f"id TEXT PRIMARY KEY, embedding FLOAT[{DIMS}] distance_metric=cosine)"
        )
        return True

    @staticmethod
    def prune(db: sqlite3.Connection) -> None:
        db.execute("DELETE FROM memory_vec WHERE id NOT IN (SELECT id FROM memories)")
        db.execute("DELETE FROM memory_vec_state WHERE id NOT IN (SELECT id FROM memories)")

    @staticmethod
    def pending(db: sqlite3.Connection) -> list[tuple[str, str, str]]:
        """(id, category, text) of memories without a vector made from their current category and text."""
        return [
            (r[0], r[1], r[2])
            for r in db.execute(
                "SELECT m.id, m.category, m.text FROM memories m LEFT JOIN memory_vec_state s ON s.id = m.id "
                f"WHERE s.doc IS NOT {SAME_DOC} ORDER BY m.updated_at DESC"
            )
        ]

    @staticmethod
    def put(db: sqlite3.Connection, memory_id: str, doc: str, vector: list[float]) -> None:
        db.execute("DELETE FROM memory_vec WHERE id = ?", (memory_id,))
        db.execute("INSERT INTO memory_vec (id, embedding) VALUES (?, ?)", (memory_id, pack(vector)))
        db.execute("INSERT OR REPLACE INTO memory_vec_state VALUES (?, ?)", (memory_id, doc))

    @staticmethod
    def nearest(db: sqlite3.Connection, vector: list[float], k: int) -> list[tuple[str, float]]:
        rows = db.execute(
            "SELECT id, distance FROM memory_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (pack(vector), k),
        )
        return [(r[0], r[1]) for r in rows]

    @staticmethod
    def fresh(db: sqlite3.Connection) -> set[str]:
        """Ids whose vector was made from the memory's current text."""
        return {
            r[0]
            for r in db.execute(
                f"SELECT m.id FROM memories m JOIN memory_vec_state s ON s.id = m.id AND s.doc = {SAME_DOC}"
            )
        }

    @staticmethod
    def counts(db: sqlite3.Connection) -> tuple[int, int]:
        total = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        done = db.execute(
            f"SELECT COUNT(*) FROM memories m JOIN memory_vec_state s ON s.id = m.id AND s.doc = {SAME_DOC}"
        ).fetchone()[0]
        return done, total
