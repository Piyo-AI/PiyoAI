"""Opt-in usage counts and crash reports. This is the only module that decides what may leave the computer.

Read this file to audit telemetry; nothing else in Piyo sends or stores anything for it.

* Off until the user says yes (asked once at first run, changeable in Settings > Privacy). While off, `record`
  does nothing at all: no ID, no queue, no file.
* Every event is built from the `EVENTS` table. A field that is not in the table is dropped; a value that does
  not pass its check drops the whole event. So free text (prompts, messages, file names, URLs, keys, account
  names, exception messages) cannot get in by accident: there is no field that accepts it.
* Crash reports carry the exception's class name and a stack of `file:line in function` entries where the
  file is a path inside Piyo or a library (never a user folder). No exception message, no local variables.
* Events wait in `telemetry-queue.jsonl` in the data folder. Settings > Privacy shows the requests built from
  it (`Telemetry.requests`), which are exactly what `flush` sends. Turning telemetry off deletes the queue and
  the install ID.
* Crashes go to Sentry and usage events to PostHog (their cloud services, over HTTPS, built by hand from the
  queued events; no SDK, so nothing is collected that is not in `EVENTS`). `SENTRY_DSN`, `POSTHOG_HOST` and
  `POSTHOG_KEY` name the cloud projects; a service left empty is skipped. Nothing is sent unless the
  user opted in.
"""

from __future__ import annotations

import json
import platform
import re
import secrets
import sys
import threading
import traceback
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

from piyo import __version__
from piyo.config import data_dir

# Public client identifiers (they ship in every copy of the app by design; they can only add events).
SENTRY_DSN = (  # Sentry cloud, US region; the key in it can only submit events
    "https://4b18dbf098630b570e02c8d6339fddf5@o4510017995735040.ingest.us.sentry.io/4512198801096704"
)
POSTHOG_HOST = "https://us.i.posthog.com"  # PostHog Cloud, US region
POSTHOG_KEY = "phc_o4mWguvoNsr8Fy2nHddeRxz8DehaYaJeRhJhvojKWUcX"  # the project's public key (write-only)
SEND_TIMEOUT_S = 10.0
MAX_QUEUED = 200  # oldest events are dropped beyond this
MAX_FRAMES = 15


def _text(pattern: str, limit: int = 80) -> Callable[[Any], str]:
    rx = re.compile(pattern, re.ASCII)

    def check(value: Any) -> str:
        if not isinstance(value, str) or len(value) > limit or not rx.fullmatch(value):
            raise ValueError
        return value

    return check


def _choice(*allowed: str) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        if value not in allowed:
            raise ValueError
        return value

    return check


def _items(item: Callable[[Any], str], limit: int) -> Callable[[Any], list[str]]:
    def check(value: Any) -> list[str]:
        if not isinstance(value, list | tuple) or len(value) > limit:
            raise ValueError
        return [item(v) for v in value]

    return check


_NAME = _text(r"[a-z0-9][a-z0-9_.\-]{0,63}", 64)  # tool and skill ids: lower-case words, dots, dashes
_FRAME = _text(r"[\w.\-/<>]{1,100}:\d{1,6}(:\d{1,6})?( in [\w<>.]{1,60})?", 180)

# event name -> field name -> check. The whole telemetry surface.
EVENTS: dict[str, dict[str, Callable[[Any], Any]]] = {
    "app_start": {
        "version": _text(r"\d{1,3}\.\d{1,3}\.\d{1,3}([\-+.][\w.\-]{1,20})?", 40),
        "os": _choice("Windows", "macOS", "Linux", "other"),
        "arch": _choice("x64", "arm64", "other"),
    },
    "run": {
        "outcome": _choice(
            "done", "step_limit", "truncated", "cancelled", "error", "token_limit", "timeout"
        ),
        "tools": _items(_NAME, 60),  # names of Piyo's own tools that ran
        "skills": _items(_NAME, 20),  # built-in and catalog skill ids only
    },
    "crash": {
        "source": _choice("core", "app", "shell"),
        "type": _text(r"[A-Za-z_][\w.]{0,79}", 80),
        "frames": _items(_FRAME, MAX_FRAMES),
    },
}


def validate(kind: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """The event's data cut down to its fields, or None if anything is missing or does not pass."""
    fields = EVENTS.get(kind)
    if fields is None or not isinstance(data, dict):
        return None
    clean: dict[str, Any] = {}
    for name, check in fields.items():
        try:
            clean[name] = check(data[name])
        except (KeyError, ValueError, TypeError):
            return None
    return clean


# --- Stack traces -------------------------------------------------------------------------------------------


def frame_label(filename: str) -> str:
    """A path that can be shared: a file inside Piyo or a library, else a placeholder. Never a user folder."""
    parts = filename.replace("\\", "/").split("/")
    folders = parts[:-1]

    def last(name: str) -> int | None:
        return max((i for i, p in enumerate(folders) if p == name), default=None)

    if (i := last("site-packages")) is not None:
        return "/".join(parts[i + 1 :])
    if (i := last("piyo")) is not None:
        return "/".join(parts[i:])
    if parts[-1].endswith(".py") and any(p in ("lib", "Lib") or p.startswith("python3") for p in folders):
        return f"stdlib/{parts[-1]}"
    return "<other>"


def stack_frames(tb: TracebackType | None) -> list[str]:
    """`file:line in function` for the innermost frames of a traceback; no locals, no source lines."""
    frames = []
    for item in traceback.extract_tb(tb)[-MAX_FRAMES:]:
        frames.append(f"{frame_label(item.filename)}:{item.lineno} in {item.name}")
    return [f for f in frames if _valid(_FRAME, f)]


def _valid(check: Callable[[Any], Any], value: Any) -> bool:
    try:
        check(value)
    except ValueError:
        return False
    return True


def _os_name() -> str:
    return {"Windows": "Windows", "Darwin": "macOS", "Linux": "Linux"}.get(platform.system(), "other")


def _arch() -> str:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64", "x64"):
        return "x64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return "other"


# --- Sending ------------------------------------------------------------------------------------------------


@dataclass
class Request:
    """One HTTPS request to one service, exactly as it would be sent."""

    service: str  # "Sentry" or "PostHog"
    url: str  # empty when that service is not configured
    headers: dict[str, str]
    payload: dict[str, Any]  # what Settings > Privacy shows
    body: bytes  # the bytes on the wire
    event_ids: list[str] = field(default_factory=list)


def _sentry_target() -> tuple[str, str] | None:
    """(envelope URL, public key) from `SENTRY_DSN`, or None if unset or malformed."""
    parts = urlsplit(SENTRY_DSN)
    project = parts.path.rstrip("/").rsplit("/", 1)[-1]
    if parts.scheme != "https" or not parts.username or not parts.hostname or not project.isdigit():
        return None
    prefix = parts.path.rstrip("/").rsplit("/", 1)[0]
    port = f":{parts.port}" if parts.port else ""
    return f"https://{parts.hostname}{port}{prefix}/api/{project}/envelope/", parts.username


def _posthog_target() -> str | None:
    parts = urlsplit(POSTHOG_HOST)
    if parts.scheme != "https" or not parts.hostname or not POSTHOG_KEY:
        return None
    return f"https://{parts.hostname}{f':{parts.port}' if parts.port else ''}/batch/"


def endpoint_configured() -> bool:
    return _sentry_target() is not None or _posthog_target() is not None


_FRAME_PARTS = re.compile(r"(?P<file>[^:]+):(?P<line>\d+)(?::\d+)?(?: in (?P<function>.+))?")


def _sentry_frames(frames: list[str]) -> list[dict[str, Any]]:
    out = []
    for text in frames:
        if m := _FRAME_PARTS.fullmatch(text):
            frame: dict[str, Any] = {"filename": m["file"], "lineno": int(m["line"])}
            if m["function"]:
                frame["function"] = m["function"]
            out.append(frame)
    return out  # oldest call first, as Sentry expects


def build_requests(batch: dict[str, Any]) -> list[Request]:
    """Turn a queued batch into the requests for Sentry (crashes) and PostHog (everything else)."""
    install_id, app, events = batch["install_id"], batch["app"], batch["events"]
    requests = []
    crashes = [e for e in events if e["event"] == "crash"]
    usage = [e for e in events if e["event"] != "crash"]

    sentry = _sentry_target()
    for e in crashes:
        data = e["data"]
        event = {
            "event_id": e["id"],
            "timestamp": f"{e['day']}T00:00:00Z",
            "platform": {"core": "python", "app": "javascript"}.get(data["source"], "other"),
            "level": "error",
            "release": f"piyo@{app['version']}",
            "tags": {"source": data["source"], "os": app["os"], "arch": app["arch"]},
            "user": {"id": install_id},
            "exception": {
                "values": [
                    {
                        "type": data["type"],
                        "value": "",  # the exception message is never collected
                        "stacktrace": {"frames": _sentry_frames(data["frames"])},
                    }
                ]
            },
            "sdk": {"name": "piyo.telemetry", "version": app["version"]},
        }
        lines = [{"event_id": e["id"]}, {"type": "event"}, event]
        headers = {"Content-Type": "application/x-sentry-envelope"}
        if sentry:
            headers["X-Sentry-Auth"] = (
                f"Sentry sentry_version=7, sentry_key={sentry[1]}, sentry_client=piyo/{app['version']}"
            )
        requests.append(
            Request(
                "Sentry",
                sentry[0] if sentry else "",
                headers,
                event,
                "".join(json.dumps(line) + "\n" for line in lines).encode(),
                [e["id"]],
            )
        )

    if usage:
        posthog = _posthog_target()
        payload = {
            "api_key": POSTHOG_KEY,
            "batch": [
                {
                    "event": e["event"],
                    "distinct_id": install_id,
                    "uuid": e["id"],
                    "timestamp": f"{e['day']}T00:00:00Z",
                    "properties": {
                        **app,
                        **e["data"],
                        "$lib": "piyo.telemetry",
                        "$lib_version": app["version"],
                        "$process_person_profile": False,  # anonymous events: no person profile is built
                        "$geoip_disable": True,  # no location lookup from the IP address
                    },
                }
                for e in usage
            ],
        }
        requests.append(
            Request(
                "PostHog",
                posthog or "",
                {"Content-Type": "application/json"},
                payload,
                json.dumps(payload).encode(),
                [e["id"] for e in usage],
            )
        )
    return requests


def http_post(request: Request) -> None:
    """Send one request; raises on a network error or a non-2xx answer."""
    import httpx

    response = httpx.post(request.url, content=request.body, headers=request.headers, timeout=SEND_TIMEOUT_S)
    response.raise_for_status()


# --- The module ---------------------------------------------------------------------------------------------


class Telemetry:
    settings_file = "telemetry.json"
    queue_file = "telemetry-queue.jsonl"

    def __init__(self) -> None:
        self._lock = threading.Lock()

    # Consent ----------------------------------------------------------------------------------------------

    def _settings(self) -> dict[str, Any]:
        try:
            raw = json.loads((data_dir() / self.settings_file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def choice(self) -> str:
        """`unset` (never asked), `on` or `off`."""
        raw = self._settings()
        if raw.get("choice") == "on" and isinstance(raw.get("install_id"), str):
            return "on"
        return "off" if raw.get("choice") in ("on", "off") else "unset"

    def enabled(self) -> bool:
        return self.choice() == "on"

    def install_id(self) -> str | None:
        return self._settings().get("install_id") if self.enabled() else None

    def endpoint_configured(self) -> bool:
        return endpoint_configured()

    def clear(self) -> None:
        with self._lock:
            (data_dir() / self.queue_file).unlink(missing_ok=True)

    def set_enabled(self, enabled: bool) -> str:
        """Record the answer. On makes a random install ID; off removes the ID and everything queued."""
        with self._lock:
            if enabled:
                existing = self._settings().get("install_id")
                install_id = existing if isinstance(existing, str) else secrets.token_hex(16)
                saved = {"choice": "on", "install_id": install_id}
            else:
                saved = {"choice": "off"}
                (data_dir() / self.queue_file).unlink(missing_ok=True)
            (data_dir() / self.settings_file).write_text(json.dumps(saved), encoding="utf-8")
        return self.choice()

    # Recording --------------------------------------------------------------------------------------------

    def record(self, kind: str, data: dict[str, Any]) -> bool:
        """Queue one event if the user opted in and it passes the table. Never raises."""
        try:
            if not self.enabled():
                return False
            clean = validate(kind, data)
            if clean is None:
                return False
            event = {
                "id": uuid.uuid4().hex,
                "event": kind,
                "day": datetime.now(UTC).strftime("%Y-%m-%d"),
                "data": clean,
            }
            with self._lock:
                events = [*self._read(), event][-MAX_QUEUED:]
                path = data_dir() / self.queue_file
                path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
            return True
        except Exception:
            return False  # telemetry must never break the app

    def app_start(self) -> bool:
        return self.record("app_start", {"version": __version__, "os": _os_name(), "arch": _arch()})

    def run_finished(self, outcome: str, tools: Iterable[str], skills: Iterable[str]) -> bool:
        return self.record(
            "run", {"outcome": outcome, "tools": sorted(set(tools)), "skills": sorted(set(skills))}
        )

    def crash(
        self,
        exc: BaseException | None = None,
        source: str = "core",
        *,
        type_name: str | None = None,
        frames: list[str] | None = None,
    ) -> bool:
        """A crash from an exception object (the core) or from already-reduced parts (the app, the shell)."""
        if exc is not None:
            module = type(exc).__module__
            name = type(exc).__qualname__
            type_name = name if module == "builtins" else f"{module}.{name}"
            frames = stack_frames(exc.__traceback__)
        return self.record("crash", {"source": source, "type": type_name, "frames": frames or []})

    # Looking and sending ----------------------------------------------------------------------------------

    def _read(self) -> list[dict[str, Any]]:
        try:
            lines = (data_dir() / self.queue_file).read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                out.append(event)
        return out

    def pending(self) -> list[dict[str, Any]]:
        """What waits to be sent; empty unless telemetry is on."""
        return self._read() if self.enabled() else []

    def batch(self) -> dict[str, Any] | None:
        """What waits to be sent, with the install ID and app details; None when empty or telemetry is off."""
        events = self.pending()
        if not events:
            return None
        return {
            "install_id": self.install_id(),
            "app": {"version": __version__, "os": _os_name(), "arch": _arch()},
            "events": events,
        }

    def requests(self) -> list[Request]:
        """The exact requests a send would make, one per service that has something to receive."""
        batch = self.batch()
        return build_requests(batch) if batch else []

    def flush(self, post: Callable[[Request], None] | None = None) -> bool:
        """Send what is waiting to Sentry (crashes) and PostHog (usage). True if anything was sent.

        Does nothing while neither service is configured. A service that fails keeps its events queued for
        the next try; the other one is not held back.
        """
        if not endpoint_configured():
            return False
        post = post or http_post
        sent_ids: set[str] = set()
        for request in self.requests():
            if not request.url:
                continue
            try:
                post(request)
            except Exception:
                continue
            sent_ids.update(request.event_ids)
        if not sent_ids:
            return False
        with self._lock:
            rest = [e for e in self._read() if e.get("id") not in sent_ids]
            (data_dir() / self.queue_file).write_text(
                "".join(json.dumps(e) + "\n" for e in rest), encoding="utf-8"
            )
        return True


# --- Crash hooks --------------------------------------------------------------------------------------------


def install_crash_hooks(telemetry: Telemetry) -> None:
    """Report exceptions nothing caught (main thread and other threads), then run the normal handler."""
    previous = sys.excepthook
    previous_thread = threading.excepthook

    def on_exception(kind, value, tb) -> None:
        if not issubclass(kind, KeyboardInterrupt | SystemExit):
            telemetry.crash(value)
        previous(kind, value, tb)

    def on_thread_exception(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None and not issubclass(args.exc_type, SystemExit):
            telemetry.crash(args.exc_value)
        previous_thread(args)

    sys.excepthook = on_exception
    threading.excepthook = on_thread_exception


def read_shell_crashes(telemetry: Telemetry, path: Path) -> None:
    """Queue the desktop shell's panic notes (`<file>:<line>` per line) if opted in, then delete the file.

    The shell writes only a source location, never the panic message; the file is removed even when telemetry
    is off, so an old note cannot be reported after the user later opts in.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        path.unlink(missing_ok=True)
    except OSError:
        return
    for line in lines[:5]:
        location = line.strip()
        if location and _valid(_FRAME, location):
            telemetry.crash(source="shell", type_name="panic", frames=[location])
