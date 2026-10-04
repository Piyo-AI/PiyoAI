"""Whether the first-run setup has been finished or skipped.

Kept in the data folder (not the webview's storage) so clearing site data does not bring the screen back.
"""

from __future__ import annotations

import json

from piyo.config import data_dir


class Onboarding:
    filename = "onboarding.json"

    def done(self) -> bool:
        try:
            raw = json.loads((data_dir() / self.filename).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(raw, dict) and raw.get("done") is True

    def set_done(self, done: bool) -> bool:
        (data_dir() / self.filename).write_text(json.dumps({"done": done}), encoding="utf-8")
        return done
