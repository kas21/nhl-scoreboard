"""Weather alerts source and detector.

Publishes ``weather.alerts``: the alerts in force at the location, most serious first,
already filtered by the source settings (see ``model.select_alerts``). Empty when there
are none, so the playlist board (which requires the key non-empty) simply disappears.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ....config.models import ADVANCED
from ....data import Event, Snapshot
from ....data.source import SourceContext
from .eccc import fetch_eccc, parse_eccc
from .model import OutOfBounds, select_alerts
from .nws import fetch_nws, parse_nws

log = logging.getLogger(__name__)

ALERTS_KEY = "weather.alerts"
EVENT_KIND = "weather.alert"
GAME_PHASES = frozenset({"live", "intermission"})
DEFAULT_IGNORE = ["Marine", "Small Craft", "Rip Current", "Beach Hazards", "Gale", "Hazardous Seas", "Surf"]
Provider = Literal["nws", "eccc"]


class WeatherAlertsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Weather alerts")
    enabled: bool = Field(True, description="Poll for watches, warnings and advisories at your location (US: National Weather Service; Canada: Environment Canada)")
    provider: Literal["auto", "nws", "eccc"] = Field("auto", description="auto asks the NWS first and switches to Environment Canada when the location is outside the US")
    min_level: Literal["warning", "watch", "advisory", "statement"] = Field("advisory", description="Least serious kind of alert to keep (statements are mostly noise)")
    ignore: list[str] = Field(DEFAULT_IGNORE, description="Skip alerts whose name contains any of these words (marine ones by default)")
    poll_seconds: int = Field(300, ge=60, le=1800, json_schema_extra=ADVANCED)


def detect_alerts(prev: Snapshot, new: Snapshot):
    """A ``weather.alert`` event for every alert not in the previous snapshot.

    The payload says whether a game is on so the board can stay out of the way. Emitted
    least serious first: the event bus keeps the *last* event per kind when a poll brings
    several at once, and the one that should play is the most serious. Nothing fires on
    the first publish after boot — a warning already hours old is not news, and it is on
    the alerts board regardless.
    """
    previous = prev.get(ALERTS_KEY)
    if previous is None:
        return []
    before = {a["key"] for a in previous}
    fresh = [a for a in (new.get(ALERTS_KEY) or []) if a["key"] not in before]
    live = (new.get("main_event") or {}).get("phase") in GAME_PHASES
    ts = new.updated.get(ALERTS_KEY, 0.0)
    return [Event(EVENT_KIND, ts=ts, payload={"alert": a, "live_game": live}) for a in reversed(fresh)]


class WeatherAlertsSource:
    key: ClassVar[str] = "weather_alerts"
    config_model: ClassVar[type[BaseModel]] = WeatherAlertsConfig

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._location: tuple[float, float] | None = None
        self._fallback: Provider | None = None         # set once the NWS says the location is not theirs
        self._raw: list[dict[str, Any]] = []           # last successful fetch, unfiltered
        self.active_provider: Provider | None = None   # who answered the last successful poll

    async def run(self, ctx: SourceContext) -> None:
        while True:
            cfg: WeatherAlertsConfig = ctx.config  # type: ignore[assignment]
            loc = ctx.location
            if not cfg.enabled or loc is None:
                if loc is None:
                    ctx.log.info("weather alerts: no location configured; set latitude/longitude in Settings > Location")
                if ctx.snapshot().get(ALERTS_KEY):
                    ctx.publish_to(ALERTS_KEY, [])       # switched off: take the board down
                await ctx.sleep(60)
                continue
            await self.poll(ctx, cfg, loc)
            await ctx.sleep(cfg.poll_seconds)

    async def poll(self, ctx: SourceContext, cfg: WeatherAlertsConfig, loc: tuple[float, float]) -> None:
        """One fetch-and-publish. A failed fetch still re-filters the last good list, so an
        alert that lapsed during an outage leaves the board on time."""
        if loc != self._location:                        # moved: give the NWS another try
            self._location, self._fallback, self._raw = loc, None, []
        provider = self._provider(cfg)
        try:
            alerts = await self._fetch(ctx.http, provider, loc)
        except OutOfBounds as exc:
            if cfg.provider == "auto" and provider == "nws":
                self._fallback = "eccc"                  # fetch_eccc never raises this, so one retry is the end of it
                ctx.log.info("weather alerts: %s; using Environment Canada", exc)
                await self.poll(ctx, cfg, loc)
                return
            ctx.log.warning("weather alerts: %s", exc)
            self._publish(ctx, cfg, only_if_changed=True)
        except (httpx.HTTPError, ValueError) as exc:
            ctx.log.warning("weather alerts poll failed: %s", exc)
            self._publish(ctx, cfg, only_if_changed=True)
        else:
            self.active_provider, self._raw = provider, alerts
            self._publish(ctx, cfg)

    def _publish(self, ctx: SourceContext, cfg: WeatherAlertsConfig, only_if_changed: bool = False) -> None:
        selected = select_alerts(self._raw, cfg, self._clock())
        if only_if_changed and selected == ctx.snapshot().get(ALERTS_KEY):
            return
        ctx.publish_to(ALERTS_KEY, selected)

    def _provider(self, cfg: WeatherAlertsConfig) -> Provider:
        if cfg.provider != "auto":
            return cfg.provider
        return self._fallback or "nws"

    @staticmethod
    async def _fetch(http: httpx.AsyncClient, provider: Provider, loc: tuple[float, float]) -> list[dict[str, Any]]:
        lat, lon = loc
        if provider == "eccc":
            return parse_eccc(await fetch_eccc(http, lat, lon))
        return parse_nws(await fetch_nws(http, lat, lon))
