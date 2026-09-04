"""``GET /api/dashboard``: the snapshot trimmed to what the dashboard's info cards show.

The full snapshot is available at ``/api/snapshot``, but it carries every goal, penalty and
standings row, and the dashboard polls every few seconds. This picks the handful of fields the
cards need and groups games by day so the page can stay dumb.

Everything here is derived from the snapshot alone (plus today's date), so it is a pure
function the tests can call directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from ..config import ConfigStore
from ..data import SnapshotStore
from ..data.store import Snapshot

SPORTS = (("nhl", "NHL"), ("nfl", "NFL"), ("mlb", "MLB"))
MAX_FLIGHTS = 8
MAX_HOLIDAYS = 5
MAX_FORECAST_DAYS = 4

TEAM_FIELDS = ("abbrev", "name", "score", "record")
GAME_FIELDS = ("id", "sport", "type", "date", "start_time_utc", "phase", "period", "clock", "outcome", "series", "week")
AIRCRAFT_FIELDS = ("hex", "ident", "callsign", "registration", "airline", "type", "type_name", "altitude_ft", "altitude_m", "distance_km",
                   "distance_mi", "bearing_compass", "origin", "destination", "route", "on_ground", "sightings", "first_seen")
HOLIDAY_FIELDS = ("name", "display", "date", "days")
WEATHER_FIELDS = ("label", "temp", "feels", "humidity", "wind", "wind_dir", "precip", "is_day", "units", "short", "desc", "icon")
FORECAST_FIELDS = ("date", "hi", "lo", "pop", "short", "icon")


def local_today(timezone: str | None) -> str:
    try:
        return datetime.now(ZoneInfo(timezone)).date().isoformat() if timezone else datetime.now().astimezone().date().isoformat()
    except Exception:
        return datetime.now().astimezone().date().isoformat()


def _pick(d: dict[str, Any] | None, fields: tuple[str, ...]) -> dict[str, Any]:
    return {k: d.get(k) for k in fields if k in d} if d else {}


def _team(t: dict[str, Any] | None) -> dict[str, Any]:
    return _pick(t, TEAM_FIELDS)


def trim_game(g: dict[str, Any], sport: str, favorites: set[str], main_id: str | None) -> dict[str, Any]:
    away, home = g.get("away") or {}, g.get("home") or {}
    return {
        **_pick(g, GAME_FIELDS),
        "sport": g.get("sport") or sport,
        "away": _team(away),
        "home": _team(home),
        "favorite": bool(favorites & {away.get("abbrev"), home.get("abbrev")}),
        "main": main_id is not None and str(g.get("id")) == main_id,
    }


def games_by_day(schedule: list[dict[str, Any]], scores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge the multi-day schedule with the live slate (fresher, wins on the same id), grouped by date."""
    merged: dict[str, dict[str, Any]] = {}
    for g in [*schedule, *scores]:
        merged[str(g.get("id"))] = g
    days: dict[str, list[dict[str, Any]]] = {}
    for g in merged.values():
        days.setdefault(g.get("date") or "", []).append(g)
    return [{"date": d, "games": sorted(gs, key=lambda g: g.get("start_time_utc") or "")} for d, gs in sorted(days.items())]


def sport_summary(snap: Snapshot, sport: str, title: str, main: dict[str, Any] | None) -> dict[str, Any] | None:
    """One sport's block, or None when no source publishes for it (plugin off)."""
    if not any(k.startswith(f"{sport}.") for k in snap.data):
        return None
    summary = snap.get(f"{sport}.team_summary") or {}
    favorites = set(summary)
    main_id = str(main["id"]) if main and main.get("sport") == sport and "id" in main else None
    days = games_by_day(snap.get(f"{sport}.schedule") or [], snap.get(f"{sport}.scores") or [])
    return {
        "sport": sport,
        "title": title,
        "favorites": list(summary),
        "days": [{"date": d["date"], "games": [trim_game(g, sport, favorites, main_id) for g in d["games"]]} for d in days],
        "teams": {abbrev: {"record": s.get("record") or {}, "prev_game": s.get("prev_game"), "next_game": s.get("next_game")}
                  for abbrev, s in summary.items()},
        "season": snap.get(f"{sport}.season"),
    }


def flights_summary(snap: Snapshot) -> list[dict[str, Any]] | None:
    nearby = snap.get("flights.nearby")
    if nearby is None:
        return None
    overhead = {a.get("hex") for a in (snap.get("flights.overhead") or [])}
    return [{**_pick(a, AIRCRAFT_FIELDS), "overhead": a.get("hex") in overhead} for a in nearby[:MAX_FLIGHTS]]


def holidays_summary(snap: Snapshot) -> list[dict[str, Any]] | None:
    upcoming = snap.get("holidays.upcoming")
    if upcoming is None:
        return None
    return [_pick(h, HOLIDAY_FIELDS) for h in upcoming[:MAX_HOLIDAYS]]


def weather_summary(snap: Snapshot) -> dict[str, Any] | None:
    current = snap.get("weather.current")
    if current is None:
        return None
    return {"current": _pick(current, WEATHER_FIELDS),
            "daily": [_pick(d, FORECAST_FIELDS) for d in (snap.get("weather.daily") or [])[:MAX_FORECAST_DAYS]]}


def dashboard_summary(snap: Snapshot, today: str) -> dict[str, Any]:
    main = snap.get("main_event") or None
    return {
        "today": today,
        "main_event": {"sport": main.get("sport"), "id": main.get("id")} if main else None,
        "sports": [s for s in (sport_summary(snap, key, title, main) for key, title in SPORTS) if s],
        "flights": flights_summary(snap),
        "flight_stats": snap.get("flights.stats"),
        "holidays": holidays_summary(snap),
        "weather": weather_summary(snap),
    }


def router(config: ConfigStore, snapshots: SnapshotStore) -> APIRouter:
    api = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

    @api.get("")
    def dashboard() -> dict[str, Any]:
        return dashboard_summary(snapshots.get(), local_today(config.get().location.timezone))

    return api
