"""Semantic memory search: the download, the sqlite-vec index, the fusion with keywords and the API.

No network and no real model: the embedder is a fake that maps words to concepts (so "puppy" is near "dog"),
and the sqlite-vec extension comes from the `sqlite-vec` dev dependency.
"""

import hashlib
import http.server
import io
import json
import tarfile
import threading
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_runs import AUTH, TOKEN

from piyo.server import app as server
from piyo.store import embeddings
from piyo.store.embeddings import (
    DIMS,
    EmbeddingError,
    EmbeddingSetup,
    LlamaEmbedder,
    SemanticIndex,
    shorten,
)
from piyo.store.memory import MemorySettings, MemoryStore

sqlite_vec = pytest.importorskip("sqlite_vec")

CONCEPTS = {
    "dog": 0,
    "puppy": 0,
    "biscuit": 0,
    "pet": 0,
    "dentist": 1,
    "teeth": 1,
    "tuesday": 1,
    "dark": 2,
    "theme": 2,
    "mode": 2,
    "tea": 3,
    "chai": 3,
    "drink": 3,
}


def vector_for(text: str) -> list[float]:
    v = [0.0] * DIMS
    for word in text.lower().replace(":", " ").replace("|", " ").split():
        word = word.strip(".,?")
        if word in CONCEPTS:
            v[CONCEPTS[word]] += 1.0
        else:
            v[10 + sum(map(ord, word)) % 200] += 0.1  # other words are faint noise
    return shorten(v)


class FakeSetup:
    def __init__(self, ready=True, model="model-a"):
        self._ready, self.model = ready, model

    def ready(self):
        return self._ready

    def vec_path(self):
        return Path(sqlite_vec.loadable_path())

    def model_id(self):
        return self.model


class FakeEmbedder:
    def __init__(self):
        self.calls, self.fail = [], False

    def embed(self, texts, query=False):
        if self.fail:
            raise EmbeddingError("the model is not running")
        self.calls.append((list(texts), query))
        return [vector_for(t) for t in texts]


class Rig:
    def __init__(self, tmp_path, enabled=True):
        self.settings = MemorySettings(tmp_path / "settings.json")
        self.settings.set_semantic(enabled)
        self.setup, self.embedder = FakeSetup(), FakeEmbedder()
        self.index = SemanticIndex(self.setup, self.embedder, self.settings.semantic)
        self.store = MemoryStore(
            tmp_path / "m.db", self.settings, semantic=self.index, background_index=False
        )


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def texts(found):
    return [m.text for m in found]


def test_finds_a_memory_by_meaning_when_no_words_are_shared(rig):
    rig.store.add("The dog is called Biscuit", "person")
    rig.store.add("Prefers dark mode", "preference")
    assert rig.store.search("my puppy") == []  # no shared word and nothing indexed yet
    assert rig.store.index_pending() == 2
    assert texts(rig.store.search("my puppy")) == ["The dog is called Biscuit"]
    docs = [t for texts_, is_query in rig.embedder.calls if not is_query for t in texts_]
    assert docs == [
        "title: person | text: The dog is called Biscuit",
        "title: preference | text: Prefers dark mode",
    ]
    assert rig.embedder.calls[-1] == (
        ["my puppy"],
        True,
    )  # queries are flagged, so the real one adds its prefix


def test_before_indexing_only_keywords_match_and_pending_is_counted(rig):
    rig.store.add("The dog is called Biscuit", "person")
    assert rig.store.semantic_counts() == (0, 1)
    assert rig.store.search("puppy") == []  # nothing indexed yet, no shared word
    rig.store.index_pending()
    assert rig.store.semantic_counts() == (1, 1)


def test_keyword_and_meaning_results_are_fused(rig):
    rig.store.add("Dentist appointments are on Tuesday", "routine")
    rig.store.add("Likes chai", "preference")
    rig.store.index_pending()
    found = rig.store.search("teeth tuesday")
    assert texts(found)[0] == "Dentist appointments are on Tuesday"
    assert "Likes chai" not in texts(found)  # too far away to count as a match


def test_an_edited_memory_is_not_found_by_its_old_meaning(rig):
    item = rig.store.add("The dog is called Biscuit", "person")
    rig.store.index_pending()
    rig.store.update(item.id, text="Prefers dark mode")
    assert rig.store.search("puppy") == []  # the stale vector is ignored
    assert rig.store.semantic_counts() == (0, 1)
    assert rig.store.index_pending() == 1
    assert texts(rig.store.search("theme")) == ["Prefers dark mode"]
    assert rig.store.search("puppy") == []


def test_deleted_and_cleared_memories_leave_no_vectors(rig):
    a = rig.store.add("The dog is called Biscuit", "person")
    rig.store.add("Likes chai", "preference")
    rig.store.index_pending()
    rig.store.delete(a.id)
    assert rig.store.search("puppy") == []
    rig.store.index_pending()  # prunes
    import sqlite3

    db = sqlite3.connect(rig.store.path)
    assert db.execute("SELECT COUNT(*) FROM memory_vec_state").fetchone()[0] == 1
    rig.store.clear()
    rig.store.index_pending()
    assert db.execute("SELECT COUNT(*) FROM memory_vec_state").fetchone()[0] == 0


def test_category_filter_applies_to_meaning_hits(rig):
    rig.store.add("The dog is called Biscuit", "person")
    rig.store.add("Walks the pet every morning", "routine")
    rig.store.index_pending()
    assert texts(rig.store.search("puppy", category="routine")) == ["Walks the pet every morning"]


def test_a_failing_model_falls_back_to_keywords_and_keeps_the_text(rig):
    rig.store.add("The dog is called Biscuit", "person")
    rig.embedder.fail = True
    assert texts(rig.store.search("dog")) == ["The dog is called Biscuit"]  # keyword still works
    assert rig.store.index_pending() == 0
    assert "not running" in rig.index.last_error
    rig.embedder.fail = False
    assert rig.store.index_pending() == 1 and rig.index.last_error == ""


def test_switched_off_or_not_downloaded_means_keywords_only(tmp_path):
    off = Rig(tmp_path, enabled=False)
    off.store.add("The dog is called Biscuit", "person")
    assert off.store.index_pending() == 0 and off.embedder.calls == []
    assert off.store.search("puppy") == []
    (tmp_path / "x").mkdir()
    missing = Rig(tmp_path / "x")
    missing.setup._ready = False
    missing.store.add("Likes chai", "preference")
    assert missing.store.index_pending() == 0 and missing.store.search("drink") == []


def test_a_new_model_drops_old_vectors_and_reindexes(rig):
    rig.store.add("The dog is called Biscuit", "person")
    rig.store.index_pending()
    rig.setup.model = "model-b"
    assert rig.store.semantic_counts() == (0, 1)  # attach cleared the table
    assert rig.store.index_pending() == 1
    assert texts(rig.store.search("puppy")) == ["The dog is called Biscuit"]


def test_drop_vectors_keeps_the_text(rig):
    rig.store.add("The dog is called Biscuit", "person")
    rig.store.index_pending()
    rig.store.drop_vectors()
    assert texts(rig.store.list()) == ["The dog is called Biscuit"]
    assert rig.store.semantic_counts() == (0, 1)


def test_indexing_runs_in_the_background_after_a_write(tmp_path):
    rig = Rig(tmp_path)
    rig.store._background_index = True
    rig.store.add("The dog is called Biscuit", "person")
    for _ in range(100):
        if rig.store.semantic_counts() == (1, 1):
            break
        threading.Event().wait(0.05)
    assert rig.store.semantic_counts() == (1, 1)


def test_shorten_keeps_the_first_dims_and_normalises():
    short = shorten([3.0, 4.0] + [100.0] * 1000)
    assert len(short) == DIMS
    assert pytest.approx(sum(x * x for x in short)) == 1.0


# -- the embedder talks to a local server ----------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append((self.headers.get("Authorization"), body))
        data = [{"index": i, "embedding": [float(i + 1)] * 768} for i in range(len(body["input"]))]
        raw = json.dumps({"data": data[::-1]}).encode()  # out of order on purpose
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def test_embedder_sends_the_key_and_prefix_and_orders_results(tmp_path, monkeypatch):
    _Handler.seen = []
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    embedder = LlamaEmbedder(EmbeddingSetup(tmp_path))

    def fake_start():
        embedder._url, embedder._key = f"http://127.0.0.1:{httpd.server_address[1]}", "secret"

    monkeypatch.setattr(embedder, "_ensure_running", fake_start)
    try:
        out = embedder.embed(["a", "b"], query=True)
    finally:
        httpd.shutdown()
        embedder.close()
    auth, body = _Handler.seen[0]
    assert auth == "Bearer secret"
    assert body["input"] == [embeddings.query_text("a"), embeddings.query_text("b")]
    assert len(out) == 2 and len(out[0]) == DIMS
    assert out[0][0] == pytest.approx(
        out[0][1]
    )  # index 0 got the all-ones vector, whichever order it came in
    assert embedder.embed([]) == []


def test_embedder_without_downloads_says_so(tmp_path):
    with pytest.raises(EmbeddingError, match="not downloaded"):
        LlamaEmbedder(EmbeddingSetup(tmp_path)).embed(["x"])


# -- the download ----------------------------------------------------------------------------------


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def make_tar(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture
def pinned(monkeypatch):
    import sys

    exe = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    lib = embeddings._vec_library()
    real_lib = Path(
        sqlite_vec.loadable_path() + Path(lib).suffix
    ).read_bytes()  # so the API tests can load it
    model = b"fake weights"
    blobs = {
        "llama": make_zip({f"build/{exe}": b"#!fake", "build/ggml.dll": b"x"}),
        "sqlite-vec": make_tar({lib: real_lib, "README": b"x"}),
    }
    keys = (
        "windows-x86_64",
        "windows-aarch64",
        "linux-x86_64",
        "linux-aarch64",
        "macos-x86_64",
        "macos-aarch64",
    )
    pins = {
        "model": {
            "id": "m-1",
            "name": "Fake",
            "dims": 256,
            "approx_mb": 1,
            "url": "https://example.test/model",
            "file": "m.gguf",
            "size": len(model),
            "sha256": digest(model),
        },
        **{
            part: {
                "version": "1",
                "approx_mb": 1,
                "url": "https://example.test/{version}/{file}",
                "files": {
                    k: {"file": "x.zip" if part == "llama" else "x.tar.gz", "sha256": digest(blobs[part])}
                    for k in keys
                },
            }
            for part in ("llama", "sqlite-vec")
        },
    }
    monkeypatch.setattr(embeddings, "load_pins", lambda: pins)
    monkeypatch.setattr(embeddings, "platform_key", lambda: "windows-x86_64")
    served = {
        "https://example.test/model": model,
        "https://example.test/1/x.zip": blobs["llama"],
        "https://example.test/1/x.tar.gz": blobs["sqlite-vec"],
    }
    return pins, served


def fetcher(served, log=None):
    async def fetch(url, target, progress):
        if log is not None:
            log.append(url)
        data = served[url]
        target.write_bytes(data)
        progress(len(data), len(data))

    return fetch


async def test_first_use_downloads_checks_and_unpacks_everything(tmp_path, pinned):
    pins, served = pinned
    log, ready = [], []
    setup = EmbeddingSetup(tmp_path / "e", fetch=fetcher(served, log))
    setup.on_ready = lambda: ready.append(True)
    assert not setup.ready() and setup.status().state == "missing" and setup.status().approx_mb == 3
    await setup.ensure()
    assert setup.ready() and setup.status().state == "ready" and ready == [True]
    assert setup.server_path().name.startswith("llama-server")
    assert setup.vec_path().name == embeddings._vec_library()
    assert setup.model_path().read_bytes() == b"fake weights"
    assert not [p for p in (tmp_path / "e").iterdir() if p.name.startswith(".")]  # no temp leftovers
    log.clear()
    await setup.ensure()
    assert log == []  # nothing downloaded twice


async def test_a_download_that_does_not_match_its_hash_is_deleted_and_not_used(tmp_path, pinned):
    pins, served = pinned
    served["https://example.test/model"] = b"swapped weights!"
    setup = EmbeddingSetup(tmp_path / "e", fetch=fetcher(served))
    with pytest.raises(EmbeddingError, match="fingerprint"):
        await setup.ensure()
    assert setup.model_path() is None and not setup.ready()
    assert setup.status().state == "failed"
    assert not list((tmp_path / "e").rglob("*.gguf"))


async def test_an_archive_that_escapes_its_folder_is_refused(tmp_path, pinned):
    pins, served = pinned
    evil = make_zip({"../../escape.txt": b"x"})
    for entry in pins["llama"]["files"].values():
        entry["sha256"] = digest(evil)
    served["https://example.test/1/x.zip"] = evil
    setup = EmbeddingSetup(tmp_path / "e", fetch=fetcher(served))
    with pytest.raises(EmbeddingError):
        await setup.ensure()
    assert not (tmp_path / "escape.txt").exists()
    assert "outside" in setup.status().message


async def test_a_network_failure_is_reported_and_can_be_retried(tmp_path, pinned):
    import httpx

    pins, served = pinned
    broken = {"on": True}
    good = fetcher(served)

    async def flaky(url, target, progress):
        if broken["on"]:
            raise httpx.ConnectError("offline")
        await good(url, target, progress)

    setup = EmbeddingSetup(tmp_path / "e", fetch=flaky)
    with pytest.raises(EmbeddingError, match="internet"):
        await setup.ensure()
    broken["on"] = False
    await setup.ensure()
    assert setup.ready()


async def test_remove_deletes_the_files(tmp_path, pinned):
    pins, served = pinned
    setup = EmbeddingSetup(tmp_path / "e", fetch=fetcher(served))
    await setup.ensure()
    setup.remove()
    assert not setup.ready() and not (tmp_path / "e").exists()


def test_every_pinned_file_has_a_hash_for_every_supported_platform():
    pins = embeddings.load_pins()
    assert len(pins["model"]["sha256"]) == 64 and pins["model"]["url"].startswith("https://")
    for part in ("llama", "sqlite-vec"):
        for key, entry in pins[part]["files"].items():
            assert len(entry["sha256"]) == 64 and entry["file"], (part, key)
        assert pins[part]["url"].startswith("https://")
    assert set(pins["sqlite-vec"]["files"]) <= set(pins["llama"]["files"])
    assert pins["model"]["dims"] == DIMS


# -- the API ---------------------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path, pinned):
    pins, served = pinned
    setup = EmbeddingSetup(tmp_path / "e", fetch=fetcher(served))
    settings = MemorySettings(tmp_path / "settings.json")
    embedder = FakeEmbedder()
    store = MemoryStore(
        tmp_path / "m.db",
        settings,
        semantic=SemanticIndex(setup, embedder, settings.semantic),
        background_index=False,
    )
    app = server.create_app(TOKEN, memory=store, embedding_setup=setup, start_scheduler=False)
    with TestClient(app) as client:
        yield client, store, setup


def test_api_turning_it_on_downloads_then_indexes(api):
    client, store, setup = api
    store.add("The dog is called Biscuit", "person")
    off = client.get("/api/memory-semantic", headers=AUTH).json()
    assert off["enabled"] is False and off["state"] == "off" and off["total"] == 1 and off["approx_mb"] == 3
    assert off["downloaded"] is False

    on = client.put("/api/memory-semantic", json={"enabled": True}, headers=AUTH).json()
    assert on["enabled"] is True
    for _ in range(100):
        status = client.get("/api/memory-semantic", headers=AUTH).json()
        if status["state"] == "ready":
            break
        threading.Event().wait(0.05)
    assert status["state"] == "ready" and status["model"] == "Fake"
    store.index_pending()  # the background thread is off in tests; this is what on_ready starts
    assert client.get("/api/memory-semantic", headers=AUTH).json()["indexed"] == 1
    assert [m["text"] for m in client.get("/api/memory", params={"q": "puppy"}, headers=AUTH).json()] == [
        "The dog is called Biscuit"
    ]


def test_api_turning_it_off_and_removing(api):
    client, store, setup = api
    store.add("The dog is called Biscuit", "person")
    client.put("/api/memory-semantic", json={"enabled": True}, headers=AUTH)
    for _ in range(100):
        if setup.ready():
            break
        threading.Event().wait(0.05)
    store.index_pending()
    off = client.put("/api/memory-semantic", json={"enabled": False}, headers=AUTH).json()
    assert off["state"] == "off" and setup.ready() and off["downloaded"] is True  # files kept
    assert client.get("/api/memory", params={"q": "puppy"}, headers=AUTH).json() == []
    gone = client.delete("/api/memory-semantic", headers=AUTH).json()
    assert gone["state"] == "off" and not setup.ready() and gone["downloaded"] is False
    assert gone["indexed"] == 0
    assert [m["text"] for m in client.get("/api/memory", headers=AUTH).json()] == [
        "The dog is called Biscuit"
    ]


def test_api_needs_the_token(api):
    client, *_ = api
    assert client.get("/api/memory-semantic").status_code in (401, 403)
    assert client.put("/api/memory-semantic", json={"enabled": True}).status_code in (401, 403)
    assert client.delete("/api/memory-semantic").status_code in (401, 403)
