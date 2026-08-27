"""Weather from Open-Meteo (free, keyless) for the configured location.

Publishes ``weather.current`` and ``weather.daily`` (normalised, unit-converted).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...config.models import ADVANCED
from ...data.source import SourceContext

log = logging.getLogger(__name__)

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
FIELDS = {
    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
}

# WMO weather code -> (short label, description, icon key)
WMO = {
    0: ("CLR", "Clear", "clear"), 1: ("CLR", "Mainly clear", "clear"), 2: ("PCL", "Partly cloudy", "partly"),
    3: ("OVC", "Overcast", "cloudy"), 45: ("FOG", "Fog", "fog"), 48: ("FOG", "Rime fog", "fog"),
    51: ("DRZ", "Light drizzle", "showers"), 53: ("DRZ", "Drizzle", "showers"), 55: ("DRZ", "Heavy drizzle", "showers"),
    56: ("DRZ", "Freezing drizzle", "sleet"), 57: ("DRZ", "Freezing drizzle", "sleet"),
    61: ("RAN", "Light rain", "rain"), 63: ("RAN", "Rain", "rain"), 65: ("RAN", "Heavy rain", "rain"),
    66: ("RAN", "Freezing rain", "sleet"), 67: ("RAN", "Freezing rain", "sleet"),
    71: ("SNW", "Light snow", "snow"), 73: ("SNW", "Snow", "snow"), 75: ("SNW", "Heavy snow", "snow"), 77: ("SNW", "Snow grains", "snow"),
    80: ("SHR", "Showers", "showers"), 81: ("SHR", "Showers", "showers"), 82: ("SHR", "Heavy showers", "showers"),
    85: ("SNW", "Snow showers", "snow"), 86: ("SNW", "Snow showers", "snow"),
    95: ("STM", "Thunderstorm", "storm"), 96: ("STM", "Thunderstorm, hail", "storm"), 99: ("STM", "Thunderstorm, hail", "storm"),
}


class WeatherConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Weather")
    enabled: bool = True
    units: Literal["metric", "imperial"] = "imperial"
    label: str = Field("", max_length=16, description="Name shown on the board (e.g. your town); blank = 'WEATHER'")
    refresh_seconds: int = Field(600, ge=120, le=3600, json_schema_extra=ADVANCED)
    forecast_days: int = Field(3, ge=1, le=5)


def _c(v: float | None, imperial: bool) -> int | None:
    return None if v is None else round(v * 9 / 5 + 32 if imperial else v)


def _kmh(v: float | None, imperial: bool) -> int | None:
    return None if v is None else round(v * 0.6214 if imperial else v)


def describe(code: int | None, is_day: bool = True) -> dict[str, Any]:
    short, desc, icon = WMO.get(int(code or 0), ("---", "Unknown", "cloudy"))
    if icon == "clear" and not is_day:
        icon = "night"
    return {"code": code, "short": short, "desc": desc, "icon": icon}


def normalize(payload: dict[str, Any], cfg: WeatherConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    imp = cfg.units == "imperial"
    cur = payload.get("current") or {}
    current = {
        "label": cfg.label or "WEATHER",
        "temp": _c(cur.get("temperature_2m"), imp),
        "feels": _c(cur.get("apparent_temperature"), imp),
        "humidity": cur.get("relative_humidity_2m"),
        "wind": _kmh(cur.get("wind_speed_10m"), imp),
        "gusts": _kmh(cur.get("wind_gusts_10m"), imp),
        "wind_dir": cur.get("wind_direction_10m"),
        "precip": cur.get("precipitation"),
        "is_day": bool(cur.get("is_day", 1)),
        "units": {"temp": "F" if imp else "C", "speed": "mph" if imp else "kmh"},
        **describe(cur.get("weather_code"), bool(cur.get("is_day", 1))),
    }
    d = payload.get("daily") or {}
    daily = []
    for i, day in enumerate(d.get("time") or []):
        daily.append({
            "date": day,
            "hi": _c((d.get("temperature_2m_max") or [None])[i] if i < len(d.get("temperature_2m_max") or []) else None, imp),
            "lo": _c((d.get("temperature_2m_min") or [None])[i] if i < len(d.get("temperature_2m_min") or []) else None, imp),
            "pop": (d.get("precipitation_probability_max") or [None] * (i + 1))[i],
            "sunrise": (d.get("sunrise") or [""] * (i + 1))[i], "sunset": (d.get("sunset") or [""] * (i + 1))[i],
            **describe((d.get("weather_code") or [None] * (i + 1))[i]),
        })
    return current, daily[: cfg.forecast_days + 1]


class WeatherSource:
    key: ClassVar[str] = "weather"
    config_model: ClassVar[type[BaseModel]] = WeatherConfig

    async def run(self, ctx: SourceContext) -> None:
        while True:
            cfg: WeatherConfig = ctx.config  # type: ignore[assignment]
            loc = ctx.location
            if not cfg.enabled or loc is None:
                if loc is None:
                    ctx.log.info("weather: no location configured; set latitude/longitude in Settings > Location")
                await asyncio.sleep(60)
                continue
            params = {"latitude": loc[0], "longitude": loc[1], "timezone": ctx.timezone or "auto",
                      "forecast_days": cfg.forecast_days + 1, **FIELDS}
            try:
                resp = await ctx.http.get(OPEN_METEO, params=params, follow_redirects=True)
                resp.raise_for_status()
                current, daily = normalize(resp.json(), cfg)
                ctx.publish(current, subkey="current")
                ctx.publish(daily, subkey="daily")
            except (httpx.HTTPError, ValueError) as exc:
                ctx.log.warning("weather poll failed: %s", exc)
            await asyncio.sleep(cfg.refresh_seconds)
