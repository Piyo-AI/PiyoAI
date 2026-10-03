"""Per-model choice of whether the model can read images, for skills that require vision.

`auto` (the default, nothing stored) uses what the provider reports, which is often nothing: then the
answer is unknown and nothing is blocked. `yes` and `no` are the user's override, for example a local
vision model the provider can't describe.
"""

from __future__ import annotations

import json
from pathlib import Path

from piyo.config import data_dir

MODES = ("auto", "yes", "no")


class ModelVision:
    """Persisted overrides, `{provider_id: {model_id: "yes" | "no"}}`, re-read on each call."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or data_dir() / "model_vision.json"

    def _load(self) -> dict[str, dict[str, str]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            pid: {m: v for m, v in models.items() if v in ("yes", "no")}
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

    def resolve(self, provider_id: str, model: str, reported: bool | None = None) -> bool | None:
        """True or False when known; None when neither the user nor the provider said."""
        mode = self.get(provider_id, model)
        return reported if mode == "auto" else mode == "yes"
