"""Per-model choice of how tools are called: natively, or as text (for models without tool calling).

`auto` (the default, nothing stored) uses what the provider reports, then what a failed request taught
us, then native calling. `native` and `prompt` are the user's override.
"""

from __future__ import annotations

import json
from pathlib import Path

from piyo.config import data_dir

MODES = ("auto", "native", "prompt")


class ToolModes:
    """Persisted overrides, `{provider_id: {model_id: "native" | "prompt"}}`, re-read on each call."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or data_dir() / "tool_modes.json"

    def _load(self) -> dict[str, dict[str, str]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            pid: {m: v for m, v in models.items() if v in ("native", "prompt")}
            for pid, models in raw.items()
            if isinstance(models, dict)
        }

    def get(self, provider_id: str, model: str) -> str:
        return self._load().get(provider_id, {}).get(model, "auto")

    def set(self, provider_id: str, model: str, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"Mode must be one of: {', '.join(MODES)}.")
        data = self._load()
        if mode == "auto":
            data.get(provider_id, {}).pop(model, None)
        else:
            data.setdefault(provider_id, {})[model] = mode
        self.path.write_text(json.dumps(data), encoding="utf-8")
