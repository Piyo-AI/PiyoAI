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
