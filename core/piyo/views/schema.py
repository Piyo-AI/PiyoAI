"""The view envelope and one schema per view type. Add a type here and in the app's `Views.tsx` together."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from piyo.views.text import plain_text

MAX_FALLBACK_CHARS = 2000
MAX_VIEW_BYTES = 20_000  # the whole view as JSON; a widget is a summary, not a document

Short = Annotated[str, StringConstraints(max_length=80)]
Tiny = Annotated[str, StringConstraints(max_length=16)]
# Strict: a string like "12" or True cannot stand in for a number.
Num = Annotated[float, Field(allow_inf_nan=False, strict=True)]
_CLOCK = re.compile(r"\d{2}:\d{2}")


class ViewError(ValueError):
    """A view that cannot be shown; the message says why, for the tool author."""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- weather --------------------------------------------------------------------------------------

# The picture is chosen by the app from this closed list; a tool never supplies an image.
WeatherIcon = Literal[
    "clear", "mostly-clear", "partly-cloudy", "cloudy", "fog", "drizzle", "rain", "heavy-rain", "snow",
    "thunder", "unknown",
]


class WeatherUnits(Strict):
    temp: Tiny
    wind: Tiny
    rain: Tiny


class WeatherNow(Strict):
    temp: Num | None = None
    feels_like: Num | None = None
    wind: Num | None = None
    sky: Short
    icon: WeatherIcon


class WeatherDay(Strict):
    date: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    sky: Short
    icon: WeatherIcon
    high: Num | None = None
    low: Num | None = None
    rain: Num | None = None  # precipitation, in `units.rain`
    rain_chance: Annotated[int, Field(ge=0, le=100, strict=True)] | None = None
    wind: Num | None = None
    sunrise: Annotated[str, StringConstraints(pattern=_CLOCK.pattern)] | None = None
    sunset: Annotated[str, StringConstraints(pattern=_CLOCK.pattern)] | None = None


class WeatherData(Strict):
    place: Short
    timezone: Short = ""
    units: WeatherUnits
    now: WeatherNow | None = None
    days: Annotated[list[WeatherDay], Field(max_length=7)]



# --- generic views: enough for most skills without new app code ------------------------------------------

Line = Annotated[str, StringConstraints(max_length=200)]
Url = Annotated[str, StringConstraints(max_length=500, pattern=r"^https?://[^\s<>\"']+$")]


class Link(Strict):
    """A link the user may open. Only http(s); the app shows the host so a label cannot hide where it goes."""

    text: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    url: Url


Cell = Line | Num | Link | None
Title = Annotated[str, StringConstraints(max_length=80)]


class TableData(Strict):
    title: Title | None = None
    columns: Annotated[list[Short], Field(min_length=1, max_length=8)]
    rows: Annotated[list[list[Cell]], Field(max_length=50)]

    @model_validator(mode="after")
    def _rows_fit_the_columns(self) -> TableData:
        for i, row in enumerate(self.rows):
            if len(row) != len(self.columns):
                raise ValueError(f"row {i} has {len(row)} cells but there are {len(self.columns)} columns")
        return self


class Fact(Strict):
    label: Annotated[str, StringConstraints(min_length=1, max_length=40)]
    value: Cell


class KeyValueData(Strict):
    title: Title | None = None
    items: Annotated[list[Fact], Field(min_length=1, max_length=20)]


class TimelineItem(Strict):
    when: Annotated[str, StringConstraints(max_length=30)] = ""  # shown as written, e.g. "09:00-10:00"
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    detail: Line | None = None
    tone: Literal["neutral", "good", "warn", "bad"] = "neutral"
    link: Link | None = None


class TimelineData(Strict):
    title: Title | None = None
    items: Annotated[list[TimelineItem], Field(min_length=1, max_length=40)]


class ListItem(Strict):
    text: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    checked: Annotated[bool, Field(strict=True)] | None = None  # set on any item to draw a checklist


class ListData(Strict):
    title: Title | None = None
    ordered: Annotated[bool, Field(strict=True)] = False
    items: Annotated[list[ListItem], Field(min_length=1, max_length=50)]


class MessageItem(Strict):
    sender: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    subject: Annotated[str, StringConstraints(max_length=150)] = ""
    snippet: Line | None = None
    when: Annotated[str, StringConstraints(max_length=30)] = ""
    account: Short | None = None  # which account it is in, when several are connected
    action: Literal["reply", "decide", "read", "none"] = "none"  # what the message needs from the user


class MessageListData(Strict):
    title: Title | None = None
    items: Annotated[list[MessageItem], Field(min_length=1, max_length=20)]

# type -> (newest version this build understands, schema of `data`)
VIEW_TYPES: dict[str, tuple[int, type[BaseModel]]] = {
    "weather": (1, WeatherData),
    "table": (1, TableData),
    "key-value": (1, KeyValueData),
    "timeline": (1, TimelineData),
    "list": (1, ListData),
    "message-list": (1, MessageListData),
}

# The types a model may ask for through `ui.show`. Weather is left out: it comes from the weather tool's
# own data, so a model cannot invent a forecast.
SHOWABLE = tuple(t for t in VIEW_TYPES if t != "weather")

# What `ui.show` tells the model about each shape. Kept next to the schemas; a test checks every type has one.
VIEW_DOCS = {
    "table": (
        '{"title"?, "columns": ["Shop", "Price"], '
        '"rows": [["A", "$10"], ["B", {"text": "Open", "url": "https://..."}]]}'
        " Up to 8 columns and 50 rows; a row has one cell per column;"
        " a cell is text, a number, a link or null."
    ),
    "key-value": (
        '{"title"?, "items": [{"label": "Status", "value": "In transit"}, '
        '{"label": "Page", "value": {"text": "Track", "url": "https://..."}}]} '
        "Up to 20 facts; a value is text, a number, a link or null."
    ),
    "timeline": (
        '{"title"?, "items": [{"when": "09:00-10:00", "title": "Standup", "detail"?: "Room 2", "tone"?: '
        '"neutral|good|warn|bad", "link"?: {"text", "url"}}]} In the order they happen, up to 40 items.'
    ),
    "list": (
        '{"title"?, "ordered"?: false, "items": [{"text": "Buy milk", "checked"?: false}]} '
        "Up to 50 items."
    ),
    "message-list": (
        '{"title"?, "items": [{"sender": "Ana", "subject": "Lunch?", "snippet"?: "...", "when"?: "10:12", '
        '"account"?: "me@x.com", "action"?: "reply|decide|read|none"}]} Up to 20 messages. Display only.'
    ),
}


def validate_view(raw: object) -> dict:
    """The checked view as plain data to send and store, or ViewError (unknown types and versions too)."""
    if not isinstance(raw, dict):
        raise ViewError("a view must be an object")
    unknown = set(raw) - {"type", "v", "data", "fallback"}
    if unknown:
        raise ViewError(f"unknown view field(s): {', '.join(sorted(map(str, unknown)))}")
    kind = raw.get("type")
    if not isinstance(kind, str) or kind not in VIEW_TYPES:
        raise ViewError(f"unknown view type {kind!r}; known types: {', '.join(sorted(VIEW_TYPES))}")
    newest, schema = VIEW_TYPES[kind]
    version = raw.get("v", 1)
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= newest:
        raise ViewError(f"view {kind!r} supports versions 1 to {newest}")
    fallback = raw.get("fallback", "")
    if not isinstance(fallback, str) or len(fallback) > MAX_FALLBACK_CHARS:
        raise ViewError(f"fallback must be text of at most {MAX_FALLBACK_CHARS} characters")
    try:
        data = schema.model_validate(raw.get("data")).model_dump(mode="json")
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "data"
        raise ViewError(f"view {kind!r}: {where}: {first['msg']}") from None
    view = {"type": kind, "v": version, "data": data, "fallback": fallback or plain_text(kind, data)}
    if len(repr(view)) > MAX_VIEW_BYTES:
        raise ViewError(f"view {kind!r} is too large")
    return view
