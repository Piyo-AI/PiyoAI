"""Per-model output limit (the `max_tokens` sent with each model turn).

Order of precedence: the user's own setting, then what the provider reports for the model, then a
default that every provider accepts. A value above a model's real maximum makes some providers
reject the request, so unknown models stay on the safe default until the user raises it.
"""

from __future__ import annotations

import json
from pathlib import Path

from piyo.config import data_dir

DEFAULT_OUTPUT_TOKENS = 4096
# When a provider reports a huge maximum, still ask for less: some providers (OpenRouter) hold
# credit for the full requested size.
AUTO_CEILING = 16_384
MIN_OUTPUT_TOKENS = 256
MAX_OUTPUT_TOKENS = 200_000


class ModelLimits:
    """Persisted user overrides, `{provider_id: {model_id: tokens}}`, re-read on each call."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or data_dir() / "model_limits.json"

    def _load(self) -> dict[str, dict[str, int]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, dict[str, int]] = {}
        for pid, models in raw.items():
            if not isinstance(models, dict):
                continue
            good = {
                m: n
                for m, n in models.items()
                if isinstance(n, int) and not isinstance(n, bool) and n > 0
            }
            if good:
                out[pid] = good
        return out

    def all(self) -> dict[str, dict[str, int]]:
        return self._load()

    def get(self, provider_id: str, model: str) -> int | None:
        return self._load().get(provider_id, {}).get(model)

    def set(self, provider_id: str, model: str, tokens: int | None) -> None:
        """Save an override, or remove it with `None`. Bounds are checked here."""
        data = self._load()
        if tokens is None:
            data.get(provider_id, {}).pop(model, None)
        else:
            if not MIN_OUTPUT_TOKENS <= tokens <= MAX_OUTPUT_TOKENS:
                raise ValueError(
                    f"Use a number between {MIN_OUTPUT_TOKENS} and {MAX_OUTPUT_TOKENS}."
                )
            data.setdefault(provider_id, {})[model] = tokens
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def resolve(self, provider_id: str, model: str, reported: int | None = None) -> int:
        custom = self.get(provider_id, model)
        if custom is not None:
            return min(custom, reported) if reported else custom
        if reported:
            return min(reported, AUTO_CEILING)
        return DEFAULT_OUTPUT_TOKENS
