import json
import sys
import threading

import pytest
from fastapi.testclient import TestClient

from piyo import telemetry as tm
from piyo.config import data_dir
from piyo.server.app import create_app
from piyo.telemetry import Telemetry

TOKEN = "t"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def raised() -> BaseException:
    try:
        raise ValueError("secret message with C:\\Users\\bob\\taxes.pdf and sk-abcdefghijklmnop1234")
    except ValueError as e:
        return e


def test_off_by_default_and_records_nothing():
    t = Telemetry()
    assert t.choice() == "unset"
    assert t.app_start() is False
    assert not (data_dir() / t.queue_file).exists()
    assert t.install_id() is None


def test_opt_in_makes_an_id_and_opt_out_removes_everything():
    t = Telemetry()
    t.set_enabled(True)
    first = t.install_id()
    assert first and len(first) == 32
    assert t.app_start() is True
    assert t.pending() and (data_dir() / t.queue_file).exists()
    assert t.set_enabled(True) == "on" and t.install_id() == first  # asking again keeps the ID

    t.set_enabled(False)
    assert t.choice() == "off"
    assert t.install_id() is None
    assert t.pending() == [] and not (data_dir() / t.queue_file).exists()
    assert t.app_start() is False
    t.set_enabled(True)
    assert t.install_id() != first  # a new opt-in is a new ID


def test_unknown_event_or_field_is_dropped():
    t = Telemetry()
    t.set_enabled(True)
    assert t.record("prompt", {"text": "hi"}) is False
    # extra fields are cut, a missing one drops the event
    assert t.record("run", {"outcome": "done", "tools": [], "skills": [], "prompt": "my diary"}) is True
    assert t.pending()[0]["data"] == {"outcome": "done", "tools": [], "skills": []}
    assert t.record("run", {"outcome": "done"}) is False


@pytest.mark.parametrize(
    "data",
    [
        {"outcome": "done", "tools": ["files.read", "C:\\Users\\bob"], "skills": []},  # a path as a tool name
        {"outcome": "done", "tools": [], "skills": ["https://example.com/x"]},  # a URL as a skill id
        {"outcome": "I asked about my medication", "tools": [], "skills": []},  # free text as an outcome
        {"outcome": "done", "tools": ["a"] * 61, "skills": []},  # too many
        {"outcome": "done", "tools": "files.read", "skills": []},
    ],
)
def test_free_text_cannot_enter_a_run_event(data):
    t = Telemetry()
    t.set_enabled(True)
    assert t.record("run", data) is False
    assert t.pending() == []


def test_run_event_deduplicates_and_sorts():
    t = Telemetry()
    t.set_enabled(True)
    t.run_finished("done", ["web.fetch", "files.read", "web.fetch"], ["browse-web"])
    assert t.pending()[0]["data"] == {
        "outcome": "done", "tools": ["files.read", "web.fetch"], "skills": ["browse-web"],
    }


def test_crash_keeps_class_and_frames_but_never_the_message():
    t = Telemetry()
    t.set_enabled(True)
    assert t.crash(raised()) is True
    stored = (data_dir() / t.queue_file).read_text(encoding="utf-8")
    event = json.loads(stored)["data"]
    assert event["type"] == "ValueError"
    assert event["source"] == "core"
    assert event["frames"] and event["frames"][-1].endswith(" in raised")
    for private in ("secret message", "bob", "taxes", "sk-abcdef", "C:\\Users"):
        assert private not in stored


@pytest.mark.parametrize(
    ("path", "label"),
    [
        ("D:\\Projects\\PiyoAI\\PiyoAI\\core\\piyo\\agent\\loop.py", "piyo/agent/loop.py"),
        ("/opt/app/_internal/piyo/server/app.py", "piyo/server/app.py"),
        ("C:\\Users\\bob\\proj\\.venv\\Lib\\site-packages\\httpx\\_client.py", "httpx/_client.py"),
        ("/usr/lib/python3.12/asyncio/base_events.py", "stdlib/base_events.py"),
        ("C:\\Users\\bob\\Documents\\notes.py", "<other>"),
        ("<string>", "<other>"),
    ],
)
def test_frame_labels_never_show_a_user_folder(path, label):
    assert tm.frame_label(path) == label


def test_crash_from_the_app_or_shell_is_validated_too():
    t = Telemetry()
    t.set_enabled(True)
    assert t.crash(source="app", type_name="TypeError", frames=["index.js:1:2345"]) is True
    assert t.crash(source="app", type_name="TypeError", frames=["Cannot read secrets of bob"]) is False
    assert t.crash(source="app", type_name="Bad name with spaces", frames=[]) is False
    assert t.crash(source="elsewhere", type_name="TypeError", frames=[]) is False


def test_queue_is_capped():
    t = Telemetry()
    t.set_enabled(True)
    for _ in range(tm.MAX_QUEUED + 25):
        t.run_finished("done", [], [])
    assert len(t.pending()) == tm.MAX_QUEUED


def test_nothing_is_sent_without_an_endpoint():
    t = Telemetry()
    t.set_enabled(True)
    t.app_start()
    sent = []
    assert t.flush(sent.append) is False
    assert sent == [] and len(t.pending()) == 1
    assert not t.endpoint_configured()


SENTRY = "https://abc123publickey@o42.ingest.sentry.io/4507"
POSTHOG = "https://eu.i.posthog.com"


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setattr(tm, "SENTRY_DSN", SENTRY)
    monkeypatch.setattr(tm, "POSTHOG_HOST", POSTHOG)
    monkeypatch.setattr(tm, "POSTHOG_KEY", "phc_publickey")


def opted_in_with_events():
    t = Telemetry()
    t.set_enabled(True)
    t.app_start()
    t.run_finished("done", ["files.read"], ["browse-web"])
    t.crash(raised())
    return t


def test_crashes_go_to_sentry_and_usage_to_posthog(cloud):
    t = opted_in_with_events()
    sentry, posthog = sorted(t.requests(), key=lambda r: r.service)[::-1]
    assert (sentry.service, posthog.service) == ("Sentry", "PostHog")

    assert sentry.url == "https://o42.ingest.sentry.io/api/4507/envelope/"
    assert "sentry_key=abc123publickey" in sentry.headers["X-Sentry-Auth"]
    header, item, event = (json.loads(line) for line in sentry.body.decode().splitlines())
    assert item == {"type": "event"} and header["event_id"] == event["event_id"]
    error = event["exception"]["values"][0]
    assert error["type"] == "ValueError" and error["value"] == ""
    assert error["stacktrace"]["frames"][-1]["function"] == "raised"
    assert event["user"] == {"id": t.install_id()} and event["release"].startswith("piyo@")
    assert event["tags"]["os"] in ("Windows", "macOS", "Linux", "other")

    assert posthog.url == "https://eu.i.posthog.com/batch/"
    body = json.loads(posthog.body)
    assert body == posthog.payload and body["api_key"] == "phc_publickey"
    assert [e["event"] for e in body["batch"]] == ["app_start", "run"]
    first = body["batch"][1]
    assert first["distinct_id"] == t.install_id()
    assert first["properties"]["tools"] == ["files.read"] and first["properties"]["skills"] == ["browse-web"]
    assert first["properties"]["$geoip_disable"] is True
    assert first["properties"]["$process_person_profile"] is False


def test_nothing_in_a_request_carries_private_text(cloud):
    t = Telemetry()
    t.set_enabled(True)
    t.crash(raised())
    wire = b"".join(r.body for r in t.requests()).decode()
    for private in ("secret message", "bob", "taxes", "sk-abcdef", "C:\\Users", "\\\\Users"):
        assert private not in wire


def test_flush_sends_each_service_then_clears_what_was_sent(cloud):
    t = opted_in_with_events()
    sent = []
    assert t.flush(sent.append) is True
    assert sorted(r.service for r in sent) == ["PostHog", "Sentry"]
    assert t.pending() == []


def test_one_failing_service_keeps_only_its_own_events(cloud):
    t = opted_in_with_events()

    def post(request):
        if request.service == "PostHog":
            raise OSError("down")

    assert t.flush(post) is True
    assert [e["event"] for e in t.pending()] == ["app_start", "run"]  # the crash went; usage waits
    assert t.flush(lambda r: (_ for _ in ()).throw(OSError("down"))) is False
    assert len(t.pending()) == 2


def test_a_service_without_settings_is_skipped(monkeypatch):
    monkeypatch.setattr(tm, "SENTRY_DSN", SENTRY)  # PostHog left empty
    t = opted_in_with_events()
    sent = []
    t.flush(sent.append)
    assert [r.service for r in sent] == ["Sentry"]
    assert [e["event"] for e in t.pending()] == ["app_start", "run"]


BAD_DSNS = ["", "http://key@host/1", "https://host/1", "https://key@host/abc", "garbage"]


@pytest.mark.parametrize("dsn", BAD_DSNS)
def test_a_malformed_sentry_dsn_counts_as_not_configured(monkeypatch, dsn):
    monkeypatch.setattr(tm, "SENTRY_DSN", dsn)
    assert tm.endpoint_configured() is False


def fake_httpx_post(monkeypatch, handler):
    """Make `httpx.post` talk to `handler` instead of the network."""
    import httpx

    def post(url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(httpx, "post", post)


def test_the_http_sender_posts_the_wire_bytes(cloud, monkeypatch):
    import httpx

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.content))
        return httpx.Response(200, json={})

    fake_httpx_post(monkeypatch, handler)
    t = opted_in_with_events()
    assert t.flush() is True  # the default sender
    assert sorted(u for u, _ in seen) == [
        "https://eu.i.posthog.com/batch/",
        "https://o42.ingest.sentry.io/api/4507/envelope/",
    ]
    assert t.pending() == []


def test_a_rejected_post_keeps_the_events(cloud, monkeypatch):
    import httpx

    calls = []

    def reject(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(500)

    fake_httpx_post(monkeypatch, reject)
    t = opted_in_with_events()
    assert t.flush() is False
    assert len(calls) == 2 and len(t.pending()) == 3


def test_unhandled_exceptions_reach_the_hooks(monkeypatch):
    t = Telemetry()
    t.set_enabled(True)
    monkeypatch.setattr(sys, "excepthook", lambda *a: None)
    monkeypatch.setattr(threading, "excepthook", lambda *a: None)
    tm.install_crash_hooks(t)
    e = raised()
    sys.excepthook(type(e), e, e.__traceback__)
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)  # not a crash
    threading.excepthook(threading.ExceptHookArgs((type(e), e, e.__traceback__, None)))
    assert [ev["data"]["type"] for ev in t.pending()] == ["ValueError", "ValueError"]


def test_shell_crash_note_is_read_once_and_deleted(tmp_path):
    t = Telemetry()
    note = tmp_path / "shell-crash.log"
    note.write_text("src/rollback.rs:42:9\nthe panic said: my password is hunter2\n", encoding="utf-8")
    tm.read_shell_crashes(t, note)  # off: deleted, nothing queued
    assert not note.exists() and t.pending() == []

    t.set_enabled(True)
    note.write_text("src/rollback.rs:42:9\nthe panic said: my password is hunter2\n", encoding="utf-8")
    tm.read_shell_crashes(t, note)
    assert not note.exists()
    events = t.pending()
    assert len(events) == 1
    assert events[0]["data"] == {"source": "shell", "type": "panic", "frames": ["src/rollback.rs:42:9"]}


# --- API ---------------------------------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(create_app(TOKEN, start_scheduler=False), raise_server_exceptions=False)


def test_api_requires_the_token(client):
    assert client.get("/api/telemetry").status_code == 401
    assert client.put("/api/telemetry", json={"enabled": True}).status_code == 401
    assert client.post("/api/telemetry/ui-error", json={}).status_code == 401


def test_api_flow(client):
    out = client.get("/api/telemetry", headers=AUTH).json()
    assert out == {
        "choice": "unset", "install_id": None, "endpoint_configured": False, "events": [], "sends": [],
    }

    on = client.put("/api/telemetry", json={"enabled": True}, headers=AUTH).json()
    assert on["choice"] == "on" and len(on["install_id"]) == 32

    assert client.post(
        "/api/telemetry/ui-error", json={"name": "TypeError", "frames": ["index.js:1:99"]}, headers=AUTH
    ).status_code == 204
    shown = client.get("/api/telemetry", headers=AUTH).json()
    assert [e["event"] for e in shown["events"]] == ["crash"]

    assert client.delete("/api/telemetry/pending", headers=AUTH).json()["events"] == []
    off = client.put("/api/telemetry", json={"enabled": False}, headers=AUTH).json()
    assert off["choice"] == "off" and off["install_id"] is None


def test_ui_error_is_ignored_while_off(client):
    client.post("/api/telemetry/ui-error", json={"name": "TypeError", "frames": []}, headers=AUTH)
    assert client.get("/api/telemetry", headers=AUTH).json()["events"] == []


def test_startup_queues_the_start_event_only_when_opted_in():
    t = Telemetry()
    with TestClient(create_app(TOKEN, start_scheduler=False, telemetry=t)):
        pass
    assert t.pending() == []
    t.set_enabled(True)
    with TestClient(create_app(TOKEN, start_scheduler=False, telemetry=t)):
        pass
    assert [e["event"] for e in t.pending()] == ["app_start"]


def test_unhandled_server_error_is_reported_without_its_message():
    t = Telemetry()
    t.set_enabled(True)
    app = create_app(TOKEN, start_scheduler=False, telemetry=t)

    @app.get("/boom")
    def boom():
        raise RuntimeError("the user's diary says hunter2")

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500 and "hunter2" not in r.text
    event = t.pending()[-1]
    assert event["data"]["type"] == "RuntimeError"
    assert "hunter2" not in json.dumps(event)


# --- What a run reports ------------------------------------------------------------------------------------


def test_a_finished_run_reports_tool_and_skill_names_only():
    from piyo.models.turn import ToolCall, TurnDone
    from piyo.tools import Tool

    async def lookup(args, ctx):
        return "the diary says hunter2"

    async def turn_fn(provider, model, messages, tools, system, max_tokens):
        if not any(m.role == "tool" for m in messages):
            yield TurnDone(tool_calls=[ToolCall(id="c1", name="diary.lookup", arguments={"q": "hunter2"})])
        else:
            yield TurnDone(text="done")

    t = Telemetry()
    t.set_enabled(True)
    app = create_app(TOKEN, turn_fn=turn_fn, start_scheduler=False, telemetry=t)
    app.state.tools.register(Tool("diary.lookup", "Look", lookup, core=True))
    client = TestClient(app)
    with client.websocket_connect(f"/ws/chat?token={TOKEN}") as ws:
        ws.send_json({"provider": "ollama", "model": "m", "message": "what does my diary say about hunter2?"})
        while ws.receive_json()["type"] != "done":
            pass
    (event,) = t.pending()
    assert event["event"] == "run"
    assert event["data"] == {"outcome": "done", "tools": ["diary.lookup"], "skills": []}
    assert "hunter2" not in json.dumps(event) and "diary says" not in json.dumps(event)


def test_server_reads_the_shells_panic_note_only_under_its_own_name(tmp_path, monkeypatch):
    t = Telemetry()
    t.set_enabled(True)
    other = tmp_path / "important.txt"
    other.write_text("src/lib.rs:1:1\n", encoding="utf-8")
    monkeypatch.setenv("PIYO_SHELL_CRASH_FILE", str(other))
    with TestClient(create_app(TOKEN, start_scheduler=False, telemetry=t)):
        pass
    assert other.exists()  # never deleted: it is not the shell's note

    note = tmp_path / "shell-crash.log"
    note.write_text("tauri-2.1.0/src/lib.rs:3:4\n", encoding="utf-8")
    monkeypatch.setenv("PIYO_SHELL_CRASH_FILE", str(note))
    with TestClient(create_app(TOKEN, start_scheduler=False, telemetry=t)):
        pass
    assert not note.exists()
    kinds = [(e["event"], e["data"].get("source")) for e in t.pending()]
    assert ("crash", "shell") in kinds
