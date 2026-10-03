"""Per-model price in US dollars per million tokens, for the cost shown on a run.

Order of precedence: the user's own setting, then what the provider reports (OpenRouter does), else
unknown. Local providers are free and never need a price.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from piyo.config import data_dir

MAX_PRICE = 100_000.0


@dataclass(frozen=True)
class Price:
    input: float  # USD per million input tokens
    output: float  # USD per million output tokens

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input + output_tokens * self.output) / 1_000_000


class ModelPrices:
    """Persisted user overrides, `{provider_id: {model_id: [input, output]}}`, re-read on each call."""

    filename = "model_prices.json"

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or data_dir() / self.filename

    def _load(self) -> dict[str, dict[str, list[float]]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        out: dict[str, dict[str, list[float]]] = {}
        for pid, models in (raw.items() if isinstance(raw, dict) else ()):
            if not isinstance(models, dict):
                continue
            for model, pair in models.items():
                if (
                    isinstance(pair, list)
                    and len(pair) == 2
                    and all(isinstance(n, int | float) and not isinstance(n, bool) for n in pair)
                    and all(0 <= n <= MAX_PRICE for n in pair)
                ):
                    out.setdefault(pid, {})[model] = [float(n) for n in pair]
        return out

    def get(self, provider_id: str, model: str) -> Price | None:
        pair = self._load().get(provider_id, {}).get(model)
        return Price(*pair) if pair else None

    def set(self, provider_id: str, model: str, price: Price | None) -> None:
        """Save an override, or remove it with `None`."""
        data = self._load()
        if price is None:
            data.get(provider_id, {}).pop(model, None)
        else:
            if not (0 <= price.input <= MAX_PRICE and 0 <= price.output <= MAX_PRICE):
                raise ValueError(f"Use prices between 0 and {MAX_PRICE:g} dollars per million tokens.")
            data.setdefault(provider_id, {})[model] = [price.input, price.output]
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def resolve(
        self, provider_id: str, model: str, reported: Price | None = None, local: bool = False
    ) -> Price | None:
        if local:
            return Price(0.0, 0.0)
        return self.get(provider_id, model) or reported
