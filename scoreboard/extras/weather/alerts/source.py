"""Weather alerts source and detector.

Publishes ``weather.alerts``: the alerts in force at the location, most serious first,
already filtered by the source settings (see ``model.select_alerts``). Empty when there
are none, so the playlist board (which requires the key non-empty) simply disappears.
``None`` means "unknown": nothing has been fetched yet for this location, or the source
is switched off. The detector stays quiet across an unknown baseline, so alerts already
hours old when the app boots, comes back online or moves are not reported as news.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ....config.models import ADVANCED
from ....data import Event, Snapshot
from ....data.arbiter import ACTIVE
from ....data.source import SourceContext
from ....isotime import parse_iso
from .eccc import fetch_eccc, parse_eccc
from .model import OutOfBounds, link_updates, select_alerts
from .nws import fetch_nws, parse_nws

log = logging.getLogger(__name__)

ALERTS_KEY = "weather.alerts"
EVENT_KIND = "weather.alert"
DEFAULT_IGNORE = ["Marine", "Small Craft", "Rip Current", "Beach Hazards", "Gale", "Hazardous Seas", "Surf"]
OFF_RECHECK_SECONDS = 60          # how often a disabled / unlocated source looks at its config again
EXPIRY_GRACE_SECONDS = 1.0        # wake this long after an expiry so the clock has passed it
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
    several at once, and the one that should play is the most serious. Nothing fires
    across an unknown (``None``) baseline — a warning already hours old is not news, and
    it is on the alerts board regardless.
    """
    previous = prev.get(ALERTS_KEY)
    if previous is None:
        return []
    before = {a["key"] for a in previous}
    fresh = [a for a in (new.get(ALERTS_KEY) or []) if a["key"] not in before]
    live = (new.get("main_event") or {}).get("phase") in ACTIVE
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
                self._withdraw(ctx)
                await ctx.sleep(OFF_RECHECK_SECONDS)
                continue
            await self.poll(ctx, cfg, loc)
            await self._sleep_between_polls(ctx, cfg)

    async def poll(self, ctx: SourceContext, cfg: WeatherAlertsConfig, loc: tuple[float, float]) -> None:
        """One fetch-and-publish. A failed fetch still re-filters the last good list, so an
        alert that lapsed during an outage leaves the board on time. Any failure is a failed
        poll: a malformed payload must not crash the source into the restart ladder."""
        if loc != self._location:                        # moved: give the NWS another try, and start from unknown
            self._location, self._fallback, self._raw = loc, None, []
            self._withdraw(ctx)
        try:
            provider, fetched = await self._fetch_with_fallback(ctx, cfg, loc)
        except Exception as exc:
            expected = isinstance(exc, (httpx.HTTPError, ValueError))
            ctx.log.warning("weather alerts poll failed: %s", exc, exc_info=not expected)
            self._publish(ctx, cfg, only_if_changed=True)
            return
        self.active_provider = provider
        self._raw = link_updates(fetched, self._raw)
        self._publish(ctx, cfg)

    async def _fetch_with_fallback(self, ctx: SourceContext, cfg: WeatherAlertsConfig,
                                   loc: tuple[float, float]) -> tuple[Provider, list[dict[str, Any]]]:
        provider = self._provider(cfg)
        try:
            return provider, await self._fetch(ctx.http, provider, loc)
        except OutOfBounds as exc:
            if cfg.provider != "auto" or provider != "nws":
                raise
            self._fallback = "eccc"                      # fetch_eccc never raises this, so one retry is the end of it
            ctx.log.info("weather alerts: %s; using Environment Canada", exc)
            return "eccc", await self._fetch(ctx.http, "eccc", loc)

    async def _sleep_between_polls(self, ctx: SourceContext, cfg: WeatherAlertsConfig) -> None:
        """Wait ``poll_seconds``, waking whenever the soonest alert lapses to retire it, so the
        board is empty (and out of the playlist) the moment nothing is in force."""
        remaining = float(cfg.poll_seconds)
        while remaining > 0:
            nap = min(remaining, self._seconds_to_next_expiry(cfg))
            await ctx.sleep(nap, until_poll=remaining)
            remaining -= nap
            self._publish(ctx, cfg, only_if_changed=True)

    def _seconds_to_next_expiry(self, cfg: WeatherAlertsConfig) -> float:
        """Until the soonest of the alerts on show lapses (an ignored one lapsing changes nothing)."""
        now = self._clock()
        ends = (parse_iso(a.get("expires")) for a in select_alerts(self._raw, cfg, now))
        ahead = [(end - now).total_seconds() for end in ends if end is not None and end > now]
        return min(ahead) + EXPIRY_GRACE_SECONDS if ahead else math.inf

    def _publish(self, ctx: SourceContext, cfg: WeatherAlertsConfig, only_if_changed: bool = False) -> None:
        """``only_if_changed`` is the re-filter path (failed fetch, an expiry): it never turns
        an unknown baseline into a known one, which is the fetch's job."""
        selected = select_alerts(self._raw, cfg, self._clock())
        if only_if_changed:
            current = ctx.snapshot().get(ALERTS_KEY)
            if current is None or selected == current:
                return
        ctx.publish_to(ALERTS_KEY, selected)

    @staticmethod
    def _withdraw(ctx: SourceContext) -> None:
        """Take the board down and mark the baseline unknown."""
        if ctx.snapshot().get(ALERTS_KEY) is not None:
            ctx.publish_to(ALERTS_KEY, None)

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
