"""Setting up Ollama from the app: is it running, which models does it have, pull one.

Piyo never installs Ollama (the user downloads it) and only talks to an Ollama on this computer: a base URL
that points anywhere else is reported as not found. Pulling a model is a user action in the app; nothing
starts one on its own. Ollama's own HTTP API does the work (`/api/tags`, `/api/pull`): no terminal needed.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

# Small, supports tool calling, ~2 GB. The app suggests it; the user can pick another model name.
RECOMMENDED_MODEL = "llama3.2:3b"
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_TIMEOUT = httpx.Timeout(5.0, read=None)  # a pull streams for minutes; the status calls are short anyway


@dataclass
class OllamaStatus:
    # missing: nothing answering | no_models: running, nothing pulled | ready | pulling | failed
    state: str
    models: list[str] = field(default_factory=list)
    percent: int = 0
    message: str = ""
    recommended: str = RECOMMENDED_MODEL


def _root(base_url: str) -> str | None:
    """The Ollama server address (without `/v1`), only if it is on this computer."""
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "") not in _LOCAL_HOSTS:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


class OllamaSetup:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._pull: OllamaStatus | None = None
        self._task: asyncio.Task | None = None

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport)

    async def status(self, base_url: str) -> OllamaStatus:
        if self._pull is not None and self._pull.state in ("pulling", "failed"):
            return self._pull
        root = _root(base_url)
        if root is None:
            return OllamaStatus("missing", message="Piyo only sets up an Ollama running on this computer.")
        try:
            async with self._http() as http:
                res = await http.get(f"{root}/api/tags")
                res.raise_for_status()
                models = sorted(m["name"] for m in res.json().get("models", []) if "name" in m)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return OllamaStatus(
                "missing", message="Ollama is not running. Install it, start it, then check again."
            )
        if models:
            return OllamaStatus("ready", models)
        return OllamaStatus("no_models", message="Ollama is running but has no models yet.")

    def start_pull(self, base_url: str, model: str) -> OllamaStatus:
        """Begin pulling `model` in the background; a second call while one runs changes nothing."""
        if self._pull is not None and self._pull.state == "pulling":
            return self._pull
        root = _root(base_url)
        if root is None:
            raise ValueError("Piyo only sets up an Ollama running on this computer.")
        model = model.strip()
        if not _MODEL_NAME.fullmatch(model):
            raise ValueError("That is not a valid model name, for example llama3.2:3b.")
        self._pull = OllamaStatus("pulling", message=f"Downloading {model}…")
        self._task = asyncio.ensure_future(self._run(root, model))
        return self._pull

    async def _run(self, root: str, model: str) -> None:
        layers: dict[str, tuple[int, int]] = {}  # digest -> (total, completed)
        try:
            async with self._http() as http:
                body = {"model": model, "stream": True}
                async with http.stream("POST", f"{root}/api/pull", json=body) as res:
                    res.raise_for_status()
                    async for line in res.aiter_lines():
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        if event.get("error"):
                            raise RuntimeError(str(event["error"]))
                        if event.get("digest") and event.get("total"):
                            layers[event["digest"]] = (int(event["total"]), int(event.get("completed", 0)))
                            total = sum(t for t, _ in layers.values())
                            done = sum(c for _, c in layers.values())
                            percent = max(0, min(done * 100 // total, 100))
                            self._pull = OllamaStatus(
                                "pulling", percent=percent, message=f"Downloading {model}…"
                            )
        except Exception as e:
            reason = str(e) if isinstance(e, RuntimeError) else "the download did not finish"
            self._pull = OllamaStatus("failed", message=f"Could not download {model}: {reason}.")
            return
        self._pull = None  # done: the next status call reads the real list

    async def close(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
