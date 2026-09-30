"""Open-Meteo weather (no API key) for daily briefings."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


def geocode_city(city: str) -> dict[str, Any] | None:
    """Resolve a city name to lat/lon via Open-Meteo geocoding."""
    q = (city or "").strip()
    if not q:
        return None
    url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={quote(q)}&count=1&language=en&format=json"
    )
    try:
        with urlopen(Request(url, headers={"User-Agent": "NexusNode/1.0"}), timeout=8) as resp:
            data = __import__("json").loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    rows = data.get("results") or []
    if not rows:
        return None
    row = rows[0]
    return {
        "name": row.get("name"),
        "country": row.get("country_code") or row.get("country"),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "timezone": row.get("timezone"),
    }


def fetch_weather(
    *,
    latitude: float,
    longitude: float,
    timezone_name: str = "UTC",
) -> dict[str, Any] | None:
    """Current conditions + today high/low from Open-Meteo."""
    tz = quote(timezone_name or "UTC")
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}&longitude={longitude}"
        "&current=temperature_2m,weather_code,wind_speed_10m"
        "&daily=temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max"
        f"&timezone={tz}&forecast_days=1"
    )
    try:
        with urlopen(Request(url, headers={"User-Agent": "NexusNode/1.0"}), timeout=8) as resp:
            data = __import__("json").loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    current = data.get("current") or {}
    daily = data.get("daily") or {}
    code = current.get("weather_code")
    return {
        "temperature_c": current.get("temperature_2m"),
        "wind_kmh": current.get("wind_speed_10m"),
        "weather_code": code,
        "summary": _code_label(code),
        "today_high_c": (daily.get("temperature_2m_max") or [None])[0],
        "today_low_c": (daily.get("temperature_2m_min") or [None])[0],
        "precip_chance_pct": (daily.get("precipitation_probability_max") or [None])[0],
    }


def weather_for_city(city: str) -> dict[str, Any]:
    """Geocode + forecast; returns ``ok`` payload or error."""
    geo = geocode_city(city)
    if not geo or geo.get("latitude") is None:
        return {"ok": False, "error": f"Could not find city '{city}'"}
    wx = fetch_weather(
        latitude=float(geo["latitude"]),
        longitude=float(geo["longitude"]),
        timezone_name=str(geo.get("timezone") or "UTC"),
    )
    if not wx:
        return {"ok": False, "error": "Weather fetch failed", "place": geo}
    return {"ok": True, "place": geo, **wx}


def _code_label(code: Any) -> str:
    try:
        c = int(code)
    except (TypeError, ValueError):
        return "unknown"
    table = {
        0: "clear",
        1: "mainly clear",
        2: "partly cloudy",
        3: "overcast",
        45: "fog",
        48: "rime fog",
        51: "light drizzle",
        53: "drizzle",
        55: "heavy drizzle",
        61: "light rain",
        63: "rain",
        65: "heavy rain",
        71: "light snow",
        73: "snow",
        75: "heavy snow",
        80: "rain showers",
        81: "rain showers",
        82: "heavy showers",
        95: "thunderstorm",
        96: "thunderstorm with hail",
        99: "thunderstorm with hail",
    }
    return table.get(c, f"code {c}")
