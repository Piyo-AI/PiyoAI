"""When a scheduled job runs. Rules are small structured values, not cron strings, so the app, the model and
the user all say the same thing:

    {"kind": "once", "at": "2026-10-05T08:00"}            one time (local time unless an offset is given)
    {"kind": "daily", "time": "08:00"}
    {"kind": "weekdays", "time": "08:00"}                 Monday to Friday
    {"kind": "weekly", "days": [0, 2], "time": "18:30"}   Monday=0 ... Sunday=6
    {"kind": "every", "minutes": 90}                      at least every 5 minutes

Times are the user's local clock, DST included. All results are timezone-aware UTC.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta, tzinfo

MIN_EVERY_MINUTES = 5
_TIME = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class RuleError(ValueError):
    """Why a schedule is not valid; the message is shown to the user and the model."""


def _clock(value: object) -> str:
    match = _TIME.fullmatch(str(value or "").strip())
    if not match:
        raise RuleError("Give the time as HH:MM on a 24-hour clock, for example 08:00.")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def parse_rule(data: object) -> dict:
    """Validate and normalise a rule; raises RuleError."""
    if not isinstance(data, dict):
        raise RuleError("A schedule is required.")
    kind = data.get("kind")
    if kind == "once":
        at = str(data.get("at") or "").strip()
        try:
            datetime.fromisoformat(at)
        except ValueError:
            raise RuleError("Give the date and time as YYYY-MM-DDTHH:MM, for example 2026-10-05T08:00.") from None
        return {"kind": "once", "at": at}
    if kind in ("daily", "weekdays"):
        return {"kind": kind, "time": _clock(data.get("time"))}
    if kind == "weekly":
        days = data.get("days")
        if not isinstance(days, list) or not days or not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
            raise RuleError("Choose at least one weekday (0 = Monday ... 6 = Sunday).")
        return {"kind": "weekly", "days": sorted(set(days)), "time": _clock(data.get("time"))}
    if kind == "every":
        minutes = data.get("minutes")
        if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes < MIN_EVERY_MINUTES:
            raise RuleError(f"Repeat every {MIN_EVERY_MINUTES} minutes or more.")
        return {"kind": "every", "minutes": minutes}
    raise RuleError("Unknown schedule type. Use once, daily, weekdays, weekly or every.")


def describe(rule: dict) -> str:
    kind = rule["kind"]
    if kind == "once":
        return f"Once, {rule['at'].replace('T', ' ')}"
    if kind == "daily":
        return f"Every day at {rule['time']}"
    if kind == "weekdays":
        return f"Weekdays at {rule['time']}"
    if kind == "weekly":
        return f"Every {', '.join(_DAY_NAMES[d] for d in rule['days'])} at {rule['time']}"
    minutes = rule["minutes"]
    return f"Every {minutes // 60} hour(s)" if minutes % 60 == 0 else f"Every {minutes} minutes"


def _local(day, clock: str, tz: tzinfo | None) -> datetime:
    hour, minute = map(int, clock.split(":"))
    naive = datetime.combine(day, time(hour, minute))
    return naive.replace(tzinfo=tz) if tz else naive.astimezone()  # naive.astimezone(): the machine's zone


def next_run(rule: dict, after: datetime, tz: tzinfo | None = None) -> datetime | None:
    """The first run strictly after `after` (aware), in UTC; None when a one-time job has passed."""
    kind = rule["kind"]
    if kind == "every":
        return (after + timedelta(minutes=rule["minutes"])).astimezone(UTC)
    if kind == "once":
        at = datetime.fromisoformat(rule["at"])
        at = at if at.tzinfo else (at.replace(tzinfo=tz) if tz else at.astimezone())
        return at.astimezone(UTC) if at > after else None
    local_after = after.astimezone(tz) if tz else after.astimezone()
    allowed = {"daily": range(7), "weekdays": range(5)}.get(kind) or rule["days"]
    for offset in range(9):  # a week plus slack for DST edges
        day = local_after.date() + timedelta(days=offset)
        if day.weekday() not in allowed:
            continue
        candidate = _local(day, rule["time"], tz)
        if candidate > after:
            return candidate.astimezone(UTC)
    return None
