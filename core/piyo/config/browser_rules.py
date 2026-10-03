"""Which sites Piyo's browser may go to, and how many pages one run may open.

`deny` always wins. When `allow` is not empty, Piyo may navigate only to those sites (and their subdomains);
an empty `allow` means any public site. Sub-requests (images, scripts) are only checked against `deny`, so
an allowed page still loads its CDNs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from piyo.config import data_dir

MAX_PAGES_BOUNDS = (1, 500)
DEFAULT_MAX_PAGES = 25
MAX_ENTRIES = 100
_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


@dataclass
class BrowserRulesData:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    max_pages: int = DEFAULT_MAX_PAGES


def normalize_domain(entry: str) -> str:
    """`https://*.Example.com/path` -> `example.com`; raises ValueError when it isn't a site name."""
    host = entry.strip().lower()
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", host)
    host = host.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0].removeprefix("*.").strip(".")
    if not _HOST.match(host):
        raise ValueError(f"{entry.strip()!r} is not a site name like example.com.")
    return host


def host_matches(host: str, domains: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


class BrowserRules:
    filename = "browser_rules.json"

    def __init__(self) -> None:
        self._cache: tuple[tuple[str, int], BrowserRulesData] | None = None  # every request asks, so reuse

    def get(self) -> BrowserRulesData:
        path = data_dir() / self.filename
        try:
            stamp = (str(path), path.stat().st_mtime_ns)
        except OSError:
            stamp = (str(path), -1)
        if self._cache and self._cache[0] == stamp:
            return self._cache[1]
        out = self._read(path)
        self._cache = (stamp, out)
        return out

    def _read(self, path) -> BrowserRulesData:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        out = BrowserRulesData()
        for key in ("allow", "deny"):
            items = raw.get(key)
            if isinstance(items, list):
                clean = []
                for item in items[:MAX_ENTRIES]:
                    try:
                        clean.append(normalize_domain(item))
                    except (ValueError, AttributeError):
                        continue
                setattr(out, key, clean)
        pages = raw.get("max_pages")
        low, high = MAX_PAGES_BOUNDS
        if isinstance(pages, int) and not isinstance(pages, bool) and low <= pages <= high:
            out.max_pages = pages
        return out

    def update(
        self, allow: list[str] | None = None, deny: list[str] | None = None, max_pages: int | None = None
    ) -> BrowserRulesData:
        """`None` leaves a value alone; raises ValueError for a bad site name or an out-of-range limit."""
        current = self.get()
        for key, items in (("allow", allow), ("deny", deny)):
            if items is None:
                continue
            if len(items) > MAX_ENTRIES:
                raise ValueError(f"At most {MAX_ENTRIES} sites in a list.")
            setattr(current, key, list(dict.fromkeys(normalize_domain(i) for i in items)))
        if max_pages is not None:
            low, high = MAX_PAGES_BOUNDS
            if not low <= max_pages <= high:
                raise ValueError(f"Pages per run must be between {low} and {high}.")
            current.max_pages = max_pages
        (data_dir() / self.filename).write_text(
            json.dumps(
                {"allow": current.allow, "deny": current.deny, "max_pages": current.max_pages}, indent=2
            ),
            encoding="utf-8",
            newline="\n",
        )
        self._cache = None
        return current

    def check(self, host: str, navigation: bool = True) -> str | None:
        """Why Piyo may not go to this host, or None. Sub-requests only face the deny list."""
        rules = self.get()
        if host_matches(host, rules.deny):
            return f"{host} is on the list of sites Piyo may not visit."
        if navigation and rules.allow and not host_matches(host, rules.allow):
            return f"{host} is not on the list of sites Piyo may visit."
        return None
