"""Flight tracker: aircraft near ``location`` from adsb.lol (keyless), routes from adsbdb (keyless),
optional FlightAware AeroAPI fallback (paid, budgeted).

Publishes:
  flights.nearby    [aircraft...] sorted by distance (normalised, enriched)
  flights.overhead  [aircraft...] currently inside the overhead radius/altitude (drives the alert)
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import date
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...data import Event, Snapshot
from ...data.source import SourceContext

log = logging.getLogger(__name__)

ADSB_LOL = "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{nm}"
ADSBDB_CALLSIGN = "https://api.adsbdb.com/v0/callsign/{cs}"
AEROAPI_FLIGHT = "https://aeroapi.flightaware.com/aeroapi/flights/{ident}"
POSITIVE_TTL, NEGATIVE_TTL = 6 * 3600, 3600
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
AIRLINE_SUFFIXES = (" airlines", " airline", " airways", " air lines", " aviation")


class FlightsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Flights")
    enabled: bool = Field(True, description="Poll for aircraft (needs a location: set it in the wizard or Settings)")
    radius_km: int = Field(40, ge=5, le=250, description="Show aircraft within this distance")
    max_aircraft: int = Field(8, ge=1, le=20)
    poll_seconds: int = Field(30, ge=10, le=600)
    units: Literal["imperial", "metric"] = "imperial"
    include_on_ground: bool = False
    enrich_routes: bool = Field(True, description="Look up airline and route on adsbdb.com (free)")
    flightaware_api_key: str = Field("", description="Optional AeroAPI key for routes adsbdb doesn't know ($0.005/lookup)")
    flightaware_daily_budget: int = Field(30, ge=0, le=1000, description="Max paid lookups per day")
    overhead_alert: bool = Field(True, description="Interrupt the rotation when an aircraft passes close overhead")
    overhead_km: float = Field(3.0, ge=0.5, le=30)
    overhead_max_alt_ft: int = Field(10000, ge=500, le=45000)


# -- pure helpers -------------------------------------------------------------

def compass(bearing: float | None) -> str:
    return COMPASS[int(((bearing or 0) + 22.5) // 45) % 8] if bearing is not None else ""


def short_airline(name: str | None) -> str:
    if not name:
        return ""
    low = name.lower()
    for suf in AIRLINE_SUFFIXES:
        if low.endswith(suf) and len(name) > len(suf):
            return name[: -len(suf)]
    return name


def normalize_aircraft(raw: dict[str, Any]) -> dict[str, Any] | None:
    """adsb.lol aircraft -> our flat dict. Returns None for entries without a position."""
    if raw.get("lat") is None or raw.get("lon") is None:
        return None
    alt = raw.get("alt_baro")
    on_ground = alt == "ground"
    alt_ft = None if on_ground or alt is None else int(alt)
    gs = raw.get("gs")
    dst_nm = raw.get("dst")
    dist_km = round(float(dst_nm) * 1.852, 1) if dst_nm is not None else None
    callsign = (raw.get("flight") or "").strip().upper()
    return {
        "hex": raw.get("hex", ""),
        "callsign": callsign,
        "registration": raw.get("r") or "",
        "type": raw.get("t") or "",
        "type_name": raw.get("desc") or "",
        "operator": raw.get("ownOp") or "",
        "altitude_ft": alt_ft,
        "altitude_m": None if alt_ft is None else int(alt_ft * 0.3048),
        "speed_mph": None if gs is None else int(float(gs) * 1.151),
        "speed_kmh": None if gs is None else int(float(gs) * 1.852),
        "heading": None if raw.get("track") is None else int(raw["track"]),
        "vertical_rate_fpm": raw.get("baro_rate"),
        "distance_km": dist_km,
        "distance_mi": None if dist_km is None else round(dist_km * 0.6214, 1),
        "bearing": None if raw.get("dir") is None else int(raw["dir"]),
        "bearing_compass": compass(raw.get("dir")),
        "on_ground": on_ground,
        "lat": raw["lat"], "lon": raw["lon"],
        "airline": "", "origin": "", "destination": "", "route": "", "ident": callsign or raw.get("r") or raw.get("hex", "").upper(),
    }


def parse_adsbdb(payload: dict[str, Any]) -> dict[str, str] | None:
    fr = ((payload or {}).get("response") or {}).get("flightroute") if isinstance((payload or {}).get("response"), dict) else None
    if not fr:
        return None
    origin = (fr.get("origin") or {}).get("iata_code") or (fr.get("origin") or {}).get("icao_code") or ""
    dest = (fr.get("destination") or {}).get("iata_code") or (fr.get("destination") or {}).get("icao_code") or ""
    return {
        "airline": short_airline((fr.get("airline") or {}).get("name")),
        "origin": origin, "destination": dest,
        "route": f"{origin}-{dest}" if origin and dest else "",
        "ident": fr.get("callsign_iata") or fr.get("callsign") or "",
    }


def is_overhead(ac: dict[str, Any], cfg: FlightsConfig) -> bool:
    return (ac.get("distance_km") is not None and ac["distance_km"] <= cfg.overhead_km
            and not ac.get("on_ground") and (ac.get("altitude_ft") or 0) <= cfg.overhead_max_alt_ft)


def detect_overhead(prev: Snapshot, new: Snapshot):
    """Event detector: an aircraft newly inside the overhead zone -> flights.overhead event."""
    before = {a["hex"] for a in (prev.get("flights.overhead") or [])}
    ts = new.updated.get("flights.overhead", 0.0)
    return [Event("flights.overhead", ts=ts, payload={"aircraft": a}) for a in (new.get("flights.overhead") or []) if a["hex"] not in before]


# -- source ---------------------------------------------------------------------

class FlightsSource:
    key: ClassVar[str] = "flights"
    config_model: ClassVar[type[BaseModel]] = FlightsConfig

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict[str, str] | None]] = {}   # callsign -> (expires, enrichment)
        self._paid_day: date | None = None
        self._paid_count = 0

    async def run(self, ctx: SourceContext) -> None:
        while True:
            cfg: FlightsConfig = ctx.config  # type: ignore[assignment]
            loc = ctx.location
            if not cfg.enabled or loc is None:
                ctx.publish([], subkey="nearby")
                await asyncio.sleep(60)
                continue
            lat, lon = loc
            try:
                payload = await self._get(ctx.http, ADSB_LOL.format(lat=lat, lon=lon, nm=max(1, round(cfg.radius_km / 1.852))))
                aircraft = [a for a in (normalize_aircraft(r) for r in payload.get("ac") or []) if a]
                if not cfg.include_on_ground:
                    aircraft = [a for a in aircraft if not a["on_ground"]]
                aircraft.sort(key=lambda a: a["distance_km"] if a["distance_km"] is not None else 1e9)
                aircraft = aircraft[: cfg.max_aircraft]
                if cfg.enrich_routes:
                    aircraft = [await self._enrich(ctx.http, a, cfg) for a in aircraft]
                aircraft = [{**a, "metric": cfg.units == "metric"} for a in aircraft]
                ctx.publish(aircraft, subkey="nearby")
                ctx.publish([a for a in aircraft if is_overhead(a, cfg)] if cfg.overhead_alert else [], subkey="overhead")
            except (httpx.HTTPError, ValueError) as exc:
                ctx.log.warning("flight poll failed: %s", exc)
            await asyncio.sleep(cfg.poll_seconds)

    async def _get(self, http: httpx.AsyncClient, url: str, **kw) -> dict[str, Any]:
        resp = await http.get(url, follow_redirects=True, **kw)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}

    async def _enrich(self, http: httpx.AsyncClient, ac: dict[str, Any], cfg: FlightsConfig) -> dict[str, Any]:
        cs = ac.get("callsign")
        if not cs:
            return ac
        now = time.time()
        cached = self._cache.get(cs)
        if cached and cached[0] > now:
            info = cached[1]
        else:
            info = None
            try:
                info = parse_adsbdb(await self._get(http, ADSBDB_CALLSIGN.format(cs=cs)))
            except (httpx.HTTPError, ValueError):
                info = None
            if info is None and cfg.flightaware_api_key and self._paid_allowed(cfg):
                info = await self._flightaware(http, cs, cfg)
            self._cache[cs] = (now + (POSITIVE_TTL if info else NEGATIVE_TTL), info)
        return {**ac, **info} if info else ac

    def _paid_allowed(self, cfg: FlightsConfig) -> bool:
        today = date.today()
        if self._paid_day != today:
            self._paid_day, self._paid_count = today, 0
        if self._paid_count >= cfg.flightaware_daily_budget:
            return False
        self._paid_count += 1
        return True

    async def _flightaware(self, http: httpx.AsyncClient, cs: str, cfg: FlightsConfig) -> dict[str, str] | None:
        try:
            data = await self._get(http, AEROAPI_FLIGHT.format(ident=cs), headers={"x-apikey": cfg.flightaware_api_key})
        except (httpx.HTTPError, ValueError) as exc:
            log.debug("aeroapi failed for %s: %s", cs, exc)
            return None
        flights = data.get("flights") or []
        if not flights:
            return None
        f = flights[0]
        origin = (f.get("origin") or {}).get("code_iata") or (f.get("origin") or {}).get("code") or ""
        dest = (f.get("destination") or {}).get("code_iata") or (f.get("destination") or {}).get("code") or ""
        return {"airline": short_airline(f.get("operator") or ""), "origin": origin, "destination": dest,
                "route": f"{origin}-{dest}" if origin and dest else "", "ident": f.get("ident_iata") or cs}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))
