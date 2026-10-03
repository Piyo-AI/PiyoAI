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
DEFAULT_CONTEXT_TOKENS = 32_000
MIN_OUTPUT_TOKENS = 256
MAX_OUTPUT_TOKENS = 200_000


class ModelLimits:
    """Persisted user overrides, `{provider_id: {model_id: tokens}}`, re-read on each call."""

    filename = "model_limits.json"
    minimum = MIN_OUTPUT_TOKENS
    maximum = MAX_OUTPUT_TOKENS

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or data_dir() / self.filename

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
            if not self.minimum <= tokens <= self.maximum:
                raise ValueError(f"Use a number between {self.minimum} and {self.maximum}.")
            data.setdefault(provider_id, {})[model] = tokens
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def resolve(self, provider_id: str, model: str, reported: int | None = None) -> int:
        custom = self.get(provider_id, model)
        if custom is not None:
            return min(custom, reported) if reported else custom
        if reported:
            return min(reported, AUTO_CEILING)
        return DEFAULT_OUTPUT_TOKENS


class ContextLimits(ModelLimits):
    """Per-model context window (input + output) the user declares, e.g. a local model run with a
    small context. Without one, the provider-reported window, then a default."""

    filename = "context_limits.json"
    minimum = 1024
    maximum = 10_000_000

    def resolve(self, provider_id: str, model: str, reported: int | None = None) -> int:
        custom = self.get(provider_id, model)
        return custom or reported or DEFAULT_CONTEXT_TOKENS
