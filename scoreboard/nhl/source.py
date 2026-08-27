"""The NHL data source: polls api-web.nhle.com and publishes normalised data.

Snapshot keys published:
  nhl.scores        list of today's games (normalised)
  main_event        the favourite game to show, or None (drives the app state)
  nhl.standings     normalised standings
  nhl.team_summary  {abbrev: summary} for each favourite
  system            {"online": bool}
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..config.models import ADVANCED
from ..data.source import SourceContext
from ..logos import watch as watch_logos
from .api import NhlApi, NhlApiError
from .normalize import (
    ACTIVE_STATES,
    normalize_game,
    normalize_standings,
    records_from_standings,
    team_summary,
)
from .season import season_info
from .select import favorite_side, select_main_event
from .teams import NHL_TEAMS

log = logging.getLogger(__name__)

OFFLINE_AFTER_FAILURES = 3      # consecutive score-poll failures before we report offline

TeamAbbrev = Literal[NHL_TEAMS]  # type: ignore[valid-type]


class NhlConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="NHL")
    favorites: list[TeamAbbrev] = Field(["TOR"], description="Favourite teams, highest priority first", json_schema_extra={"x-widget": "team-picker"})
    live_interval: float = Field(5.0, ge=2, le=60, description="Seconds between polls while a favourite is playing", json_schema_extra=ADVANCED)
    idle_interval: float = Field(60.0, ge=15, le=600, description="Seconds between polls otherwise", json_schema_extra=ADVANCED)
    standings_interval: float = Field(3600.0, ge=300, description="Seconds between standings refreshes", json_schema_extra=ADVANCED)
    delay_seconds: float = Field(0.0, ge=0, le=120, description="Delay live updates to match your TV broadcast")
    show_games_within_days: int = Field(2, ge=0, le=30, description="Only show the league slate (ticker) when it is this close; further-out games stay off the panel")
    follow_preseason: bool = Field(True, description="Treat your team's preseason games like any other game")


class NhlSource:
    key: ClassVar[str] = "nhl"
    config_model: ClassVar[type[BaseModel]] = NhlConfig

    def __init__(self) -> None:
        self._standings_ready = asyncio.Event()

    async def run(self, ctx: SourceContext) -> None:
        api = NhlApi(ctx.http)
        self._standings_ready = asyncio.Event()
        await asyncio.gather(watch_logos(ctx.http, "nhl", NHL_TEAMS, ctx.log),
                             self._scores_loop(ctx, api), self._standings_loop(ctx, api))

    # -- scores + main event ------------------------------------------------

    async def _scores_loop(self, ctx: SourceContext, api: NhlApi) -> None:
        delayed: list[tuple[float, dict[str, Any], list[dict[str, Any]]]] = []
        failures = 0
        try:                                   # records come from standings; give them a head start
            await asyncio.wait_for(self._standings_ready.wait(), timeout=5)
        except TimeoutError:
            pass
        while True:
            cfg: NhlConfig = ctx.config  # type: ignore[assignment]
            main: dict[str, Any] | None = None
            try:
                payload = await api.score("now")
                records = records_from_standings(ctx.snapshot().get("nhl.standings"))
                games = [normalize_game(g, records) for g in payload.get("games") or []]
                if not cfg.follow_preseason:
                    games = [g for g in games if g["type"] != 1]
                today = _local_today(ctx)
                slate_date = payload.get("currentDate") or (games[0]["date"] if games else today)
                if _days_between(today, slate_date) > cfg.show_games_within_days:
                    games = []                                   # too far out to be "tonight's games"
                main = select_main_event(games, cfg.favorites, today=today)
                if main and main["state"] in ACTIVE_STATES:
                    main = await self._enrich(api, main, records)
                if main:
                    main = {**main, "favorite_side": favorite_side(main, cfg.favorites), "sport": "nhl"}
                self._deliver(ctx, cfg, main, games, delayed)
                failures = 0
                ctx.publish_to("system", {"online": True, "failures": 0})
            except NhlApiError as exc:
                failures += 1
                ctx.log.warning("score poll failed (%s in a row): %s", failures, exc)
                if failures >= OFFLINE_AFTER_FAILURES:
                    ctx.publish_to("system", {"online": False, "failures": failures})
                main = (ctx.snapshot().get("main_event") or None)      # keep polling cadence of last known state
            active = bool(main and main["state"] in ACTIVE_STATES)
            await asyncio.sleep(cfg.live_interval if active else cfg.idle_interval)

    async def _enrich(self, api: NhlApi, main: dict[str, Any], records: dict[str, str]) -> dict[str, Any]:
        """Add situation (power play / pulled goalie) and penalties from the landing feed."""
        try:
            landing = await api.landing(main["id"])
        except NhlApiError as exc:
            log.debug("landing fetch failed for %s: %s", main["id"], exc)
            return main
        return normalize_game(_score_shape(main, landing), records, landing)

    def _deliver(self, ctx, cfg: NhlConfig, main, games, delayed) -> None:
        """Publish now, or hold for ``delay_seconds`` so alerts line up with a TV broadcast."""
        if cfg.delay_seconds <= 0:
            delayed.clear()
            ctx.publish(games, subkey="scores")
            ctx.publish_to("nhl.main_event", main)
            return
        now = asyncio.get_event_loop().time()
        delayed.append((now, main, games))
        while delayed and now - delayed[0][0] >= cfg.delay_seconds:
            _, m, g = delayed.pop(0)
            ctx.publish(g, subkey="scores")
            ctx.publish_to("nhl.main_event", m)

    # -- standings + team summaries -----------------------------------------

    async def _standings_loop(self, ctx: SourceContext, api: NhlApi) -> None:
        while True:
            cfg: NhlConfig = ctx.config  # type: ignore[assignment]
            try:
                raw_standings = await api.standings("now")
                standings = normalize_standings(raw_standings)
                ctx.publish(standings, subkey="standings")
                self._standings_ready.set()
                today = _local_today(ctx)
                summaries = {}
                schedules = {}
                for team in cfg.favorites:
                    try:
                        schedules[team] = await api.club_schedule_season(team)
                    except NhlApiError as exc:
                        ctx.log.warning("schedule fetch failed for %s: %s", team, exc)
                        schedules[team] = None
                    summaries[team] = team_summary(team, standings, schedules[team], today)
                ctx.publish(summaries, subkey="team_summary")
                try:
                    sched_now = await api.schedule_now()
                    st_season = next((int(r.get("seasonId")) for r in raw_standings.get("standings") or [] if r.get("seasonId")), None)
                    fav = cfg.favorites[0] if cfg.favorites else None
                    ctx.publish({**season_info(sched_now, date.fromisoformat(today), st_season, schedules.get(fav), fav), "favorite": fav}, subkey="season")
                except (NhlApiError, ValueError) as exc:
                    ctx.log.warning("season info failed: %s", exc)
            except NhlApiError as exc:
                ctx.log.warning("standings poll failed: %s", exc)
                self._standings_ready.set()
            await asyncio.sleep(cfg.standings_interval)


def _local_today(ctx: SourceContext) -> str:
    """Today's date in the configured timezone (falls back to the machine's local date)."""
    tz = getattr(ctx, "timezone", None)
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz)).date().isoformat() if tz else datetime.now().astimezone().date().isoformat()
    except Exception:
        return datetime.now().astimezone().date().isoformat()


def _score_shape(main: dict[str, Any], landing: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a score-feed-like raw game from the landing payload (landing has everything the score feed has)."""
    return {
        "id": landing.get("id", main["id"]), "gameType": landing.get("gameType", main["type"]),
        "gameState": landing.get("gameState", main["state"]), "gameDate": landing.get("gameDate", main["date"]),
        "startTimeUTC": landing.get("startTimeUTC", main["start_time_utc"]),
        "awayTeam": landing.get("awayTeam") or {}, "homeTeam": landing.get("homeTeam") or {},
        "clock": landing.get("clock") or {}, "periodDescriptor": landing.get("periodDescriptor") or {},
        "gameOutcome": landing.get("gameOutcome") or {}, "situation": landing.get("situation") or {},
    }


def _days_between(today: str, other: str) -> int:
    try:
        return (date.fromisoformat(other) - date.fromisoformat(today)).days
    except ValueError:
        return 0
