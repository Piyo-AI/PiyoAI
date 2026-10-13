"""How much of the machine background agents may use at once: the resource broker's pool sizes.

Global sliders with presets (Settings > Agents & limits). A preset sets every value at once; changing one
value afterwards makes it "custom". The numbers are first guesses until measured (docs/phase-9, C).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from piyo.config import data_dir

# name: (minimum, maximum)
BOUNDS = {
    "runs": (1, 16),  # background runs at once
    "browser_contexts": (1, 12),  # fresh browser contexts at once (used from workstream C)
    "browser_tabs": (1, 60),  # tabs over all runs (used from workstream C)
    "tabs_per_run": (1, 20),  # (used from workstream C)
    "cloud_model_calls": (1, 16),  # concurrent calls per cloud provider
    "local_model_calls": (1, 4),  # concurrent calls per local provider (8 GB VRAM target: 1)
    "scripts": (1, 8),  # concurrent skill scripts
}


@dataclass(frozen=True)
class Limits:
    runs: int
    browser_contexts: int
    browser_tabs: int
    tabs_per_run: int
    cloud_model_calls: int
    local_model_calls: int
    scripts: int


PRESETS = {
    "light": Limits(2, 1, 6, 3, 2, 1, 1),
    "balanced": Limits(4, 3, 15, 5, 4, 1, 2),
    "heavy": Limits(8, 6, 30, 8, 8, 1, 4),
}
DEFAULT_PRESET = "balanced"


def check(limits: Limits) -> None:
    """Raises ValueError with a message for the user when a value is out of range."""
    for key, (low, high) in BOUNDS.items():
        value = getattr(limits, key)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{key} must be a whole number between {low} and {high}.")
    if limits.tabs_per_run > limits.browser_tabs:
        raise ValueError("Tabs per run cannot be more than the total number of tabs.")


def preset_of(limits: Limits) -> str:
    return next((name for name, values in PRESETS.items() if values == limits), "custom")


class ResourceLimits:
    filename = "resource_limits.json"

    def __init__(self) -> None:
        self._cached: Limits | None = None

    def get(self) -> Limits:
        """Saved values; a missing or damaged file means the default preset."""
        if self._cached is not None:
            return self._cached
        try:
            raw = json.loads((data_dir() / self.filename).read_text(encoding="utf-8"))
            limits = Limits(**{key: raw[key] for key in BOUNDS})
            check(limits)
        except (OSError, ValueError, TypeError, KeyError):
            limits = PRESETS[DEFAULT_PRESET]
        self._cached = limits
        return limits

    def set(self, limits: Limits) -> Limits:
        check(limits)
        (data_dir() / self.filename).write_text(json.dumps(asdict(limits)), encoding="utf-8")
        self._cached = limits
        return limits

    def use_preset(self, name: str) -> Limits:
        if name not in PRESETS:
            raise ValueError(f"Unknown preset {name!r}; pick one of {', '.join(PRESETS)}.")
        return self.set(PRESETS[name])
