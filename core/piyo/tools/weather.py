"""Weather forecasts from Open-Meteo (no account or API key).

Only the place the user asked about is sent, as a search text or as coordinates; nothing else about the
user leaves the machine. The numbers come back as plain data, and the place name is cleaned to one short line.
"""

from __future__ import annotations

import re

import httpx

from piyo.tools.base import RunContext, Tool

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 15.0
MAX_DAYS = 7
MAX_LOCATION_CHARS = 100
_COORDS = re.compile(r"\s*(-?\d{1,2}(?:\.\d+)?)\s*[,;]\s*(-?\d{1,3}(?:\.\d+)?)\s*")

# WMO weather interpretation codes, as used by Open-Meteo.
WEATHER_CODES = {
    0: "clear sky",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "freezing fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "heavy freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "showers",
    82: "violent showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "severe thunderstorm with hail",
}

# WMO code -> the picture the app draws (the closed list in piyo.views.schema.WeatherIcon).
ICONS = {
    0: "clear", 1: "mostly-clear", 2: "partly-cloudy", 3: "cloudy", 45: "fog", 48: "fog",
    51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "drizzle", 57: "drizzle",
    61: "rain", 63: "rain", 65: "heavy-rain", 66: "rain", 67: "heavy-rain",
    71: "snow", 73: "snow", 75: "snow", 77: "snow", 85: "snow", 86: "snow",
    80: "rain", 81: "rain", 82: "heavy-rain", 95: "thunder", 96: "thunder", 99: "thunder",
}  # fmt: skip

UNITS = {
    "metric": {"temperature_unit": "celsius", "wind_speed_unit": "kmh", "precipitation_unit": "mm"},
    "imperial": {"temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch"},
}
LABELS = {"metric": ("°C", "km/h", "mm"), "imperial": ("°F", "mph", "in")}
CURRENT = "temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
DAILY = (
    "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
    "precipitation_probability_max,wind_speed_10m_max,sunrise,sunset"
)


class WeatherError(Exception):
    """Message goes back to the model, so keep it actionable."""


def _clean(text: object, limit: int = 80, keep: str = "") -> str:
    """One short line of letters, digits and light punctuation; everything else is dropped."""
    text = re.sub(rf"[^\w\s,.'()\-{keep}]", "", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _num(value: object, digits: int = 0) -> str:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return "?"
    return f"{value:.{digits}f}"


def _sky(code: object) -> str:
    return WEATHER_CODES.get(code, "unknown conditions") if isinstance(code, int) else "unknown conditions"


def _icon(code: object) -> str:
    return ICONS.get(code, "unknown") if isinstance(code, int) else "unknown"


def _val(value: object, digits: int | None = None) -> float | None:
    """A number for the widget, or None; the same readings as `_num`, kept as numbers."""
    if not isinstance(value, int | float) or isinstance(value, bool) or value != value:
        return None
    return round(value, digits) if digits is not None else float(value)


def _clock(value: object) -> str | None:
    return value[-5:] if isinstance(value, str) and re.fullmatch(r".*T\d{2}:\d{2}", value) else None


def weather_view(name: str, zone: str, units: tuple[str, str, str], data: dict) -> dict:
    """The data of the `weather` view, from the same service answer the text is written from."""
    deg, wind, rain = units
    cur = data.get("current") or {}
    daily = data.get("daily") or {}

    def at(key: str, i: int) -> object:
        values = daily.get(key) or []
        return values[i] if i < len(values) else None

    days = []
    for i, day in enumerate((daily.get("time") or [])[:MAX_DAYS]):
        chance = at("precipitation_probability_max", i)
        ok = isinstance(chance, int | float) and 0 <= chance <= 100
        chance_pct = round(chance) if ok else None
        days.append(
            {
                "date": _clean(day, 12),
                "sky": _sky(at("weather_code", i)),
                "icon": _icon(at("weather_code", i)),
                "high": _val(at("temperature_2m_max", i)),
                "low": _val(at("temperature_2m_min", i)),
                "rain": _val(at("precipitation_sum", i), 1),
                "rain_chance": chance_pct,
                "wind": _val(at("wind_speed_10m_max", i)),
                "sunrise": _clock(at("sunrise", i)),
                "sunset": _clock(at("sunset", i)),
            }
        )
    now = None
    if cur:
        now = {
            "temp": _val(cur.get("temperature_2m")),
            "feels_like": _val(cur.get("apparent_temperature")),
            "wind": _val(cur.get("wind_speed_10m")),
            "sky": _sky(cur.get("weather_code")),
            "icon": _icon(cur.get("weather_code")),
        }
    return {
        "place": name[:80],
        "timezone": zone[:80],
        "units": {"temp": deg, "wind": wind, "rain": rain},
        "now": now,
        "days": days,
    }


def _label(place: dict) -> str:
    parts = (_clean(place.get(k)) for k in ("name", "admin1", "country"))
    return ", ".join(p for p in parts if p)


def _day_line(daily: dict, i: int, units: tuple[str, str, str]) -> str:
    deg, wind, rain = units

    def at(key: str) -> object:
        values = daily.get(key) or []
        return values[i] if i < len(values) else None

    chance = at("precipitation_probability_max")
    chance_text = f" ({_num(chance)}% chance)" if chance is not None else ""
    rise, sset = at("sunrise"), at("sunset")
    both = isinstance(rise, str) and isinstance(sset, str)
    sun = f", sunrise {rise[-5:]}, sunset {sset[-5:]}" if both else ""
    return (
        f"{_clean(daily['time'][i], 12)}: {_sky(at('weather_code'))}, "
        f"{_num(at('temperature_2m_min'))} to {_num(at('temperature_2m_max'))}{deg}, "
        f"precipitation {_num(at('precipitation_sum'), 1)} {rain}{chance_text}, "
        f"wind up to {_num(at('wind_speed_10m_max'))} {wind}{sun}."
    )


class WeatherTools:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def _get(self, url: str, params: dict) -> dict:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=TIMEOUT) as client:
                res = await client.get(url, params=params)
        except httpx.TimeoutException:
            raise WeatherError("The weather service did not answer in time. Try again.") from None
        except httpx.HTTPError:
            raise WeatherError("Could not reach the weather service. Check the connection.") from None
        if res.status_code == 429:
            raise WeatherError("The weather service's rate limit was reached. Try again in a few minutes.")
        if res.status_code >= 400:
            raise WeatherError(f"The weather service answered with HTTP {res.status_code}.")
        try:
            body = res.json()
        except ValueError:
            raise WeatherError("The weather service sent an unreadable answer.") from None
        return body if isinstance(body, dict) else {}

    async def _place(self, location: str) -> tuple[float, float, str, list[str]]:
        """(latitude, longitude, display name, other matches)"""
        if m := _COORDS.fullmatch(location):
            lat, lon = float(m.group(1)), float(m.group(2))
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise WeatherError("Those coordinates are out of range.")
            return lat, lon, f"{lat:g}, {lon:g}", []
        reply = await self._get(GEOCODE_URL, {"name": location, "count": 3, "language": "en"})
        found = reply.get("results")
        if not found or not isinstance(found, list):
            raise WeatherError(
                f"No place found for {location!r}. Ask the user for a city name or a city and country."
            )
        try:
            first = found[0]
            lat, lon = float(first["latitude"]), float(first["longitude"])
            return lat, lon, _label(first), [_label(r) for r in found[1:]]
        except (KeyError, TypeError, ValueError):
            raise WeatherError("The weather service sent an unreadable place.") from None

    async def forecast(self, args: dict, ctx: RunContext) -> str:
        location = args.get("location")
        if not isinstance(location, str) or not location.strip():
            raise WeatherError("A location is required. Ask the user which city they want.")
        days = args.get("days", 1)
        days = days if isinstance(days, int) and not isinstance(days, bool) else 1
        units = args.get("units", "metric")
        if units not in UNITS:
            raise WeatherError("units must be 'metric' or 'imperial'.")
        lat, lon, name, others = await self._place(location.strip()[:MAX_LOCATION_CHARS])
        data = await self._get(
            FORECAST_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "timezone": "auto",
                "forecast_days": max(1, min(days, MAX_DAYS)),
                "current": CURRENT,
                "daily": DAILY,
                **UNITS[units],
            },
        )
        labels = LABELS[units]
        deg, wind, _ = labels
        zone = _clean(data.get("timezone"), 40, "/+") or "unknown"
        lines = [f"Weather for {name} (time zone {zone}). Units: {', '.join(labels)}."]
        if cur := data.get("current"):
            lines.append(
                f"Now: {_num(cur.get('temperature_2m'))}{deg} "
                f"(feels like {_num(cur.get('apparent_temperature'))}{deg}), "
                f"{_sky(cur.get('weather_code'))}, wind {_num(cur.get('wind_speed_10m'))} {wind}."
            )
        daily = data.get("daily") or {}
        lines += [_day_line(daily, i, labels) for i in range(len(daily.get("time") or []))]
        if len(lines) == 1:
            lines.append("The service returned no forecast data.")
        if others:
            lines.append(f"Matched the first of several places. Others: {'; '.join(others)}.")
        if cur or daily.get("time"):
            ctx.attach_view("weather", weather_view(name, zone if zone != "unknown" else "", labels, data))
        return "\n".join(lines)


def weather_tools(transport: httpx.AsyncBaseTransport | None = None) -> list[Tool]:
    impl = WeatherTools(transport)
    return [
        Tool(
            name="weather.forecast",
            description=(
                "Current weather and a daily forecast (1 to 7 days) for a place, from Open-Meteo. Give a "
                "city name (add the country if it is ambiguous) or 'latitude,longitude'. Only the place is "
                "sent."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City, or 'lat,lon'"},
                    "days": {"type": "integer", "description": "1 to 7, default 1"},
                    "units": {"type": "string", "enum": ["metric", "imperial"]},
                },
                "required": ["location"],
            },
            handler=impl.forecast,
        )
    ]
