"""Run-wide settings: the per-run budget and the "local only" switch.

A run stops cleanly when it reaches any budget limit; the user can raise them here.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from piyo.config import data_dir

# name: (default, minimum, maximum)
BOUNDS = {
    "max_steps": (20, 1, 200),
    "max_tokens": (200_000, 1_000, 10_000_000),  # input + output over all model turns of a run
    "timeout_s": (600, 10, 86_400),  # wall clock, not counting time spent waiting for an approval
}


@dataclass
class RunSettingsData:
    max_steps: int = BOUNDS["max_steps"][0]
    max_tokens: int = BOUNDS["max_tokens"][0]
    timeout_s: int = BOUNDS["timeout_s"][0]
    local_only: bool = False


class RunSettings:
    filename = "run_settings.json"

    def get(self) -> RunSettingsData:
        """Saved values, falling back to defaults for anything missing or invalid."""
        try:
            raw = json.loads((data_dir() / self.filename).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        out = RunSettingsData()
        for key, (_, low, high) in BOUNDS.items():
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
                setattr(out, key, value)
        out.local_only = raw.get("local_only") is True
        return out

    def update(self, **changes: int | bool | None) -> RunSettingsData:
        """Apply the given changes (`None` leaves a value alone); raises ValueError when out of range."""
        current = self.get()
        for key, value in changes.items():
            if value is None:
                continue
            if key == "local_only":
                current.local_only = bool(value)
                continue
            _, low, high = BOUNDS[key]
            if not low <= value <= high:
                raise ValueError(f"{key} must be between {low} and {high}.")
            setattr(current, key, value)
        (data_dir() / self.filename).write_text(json.dumps(asdict(current)), encoding="utf-8")
        return current
