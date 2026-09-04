"""MLB data source (MLB Stats API). Publishes mlb.scores, mlb.main_event, mlb.standings,
mlb.team_summary and mlb.season."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any, ClassVar, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from ..config.models import ADVANCED
from ..data.source import SourceContext
from ..logos import watch as watch_logos
from ..nhl.select import favorite_side, select_main_event
from .api import MlbApi, MlbApiError
from .normalize import (
    enrich_from_feed,
    normalize_schedule,
    normalize_standings,
    season_info,
    team_summary,
)
from .teams import MLB_TEAMS, TEAM_IDS

TeamAbbrev = Literal[MLB_TEAMS]  # type: ignore[valid-type]
ACTIVE_STATES = frozenset({"PRE", "LIVE"})
SUMMARY_LOOKBACK_DAYS = 10
SUMMARY_LOOKAHEAD_DAYS = 14


class MlbConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="MLB")
    enabled: bool = True
    favorites: list[TeamAbbrev] = Field([], description="Favourite teams, highest priority first", json_schema_extra={"x-widget": "team-picker"})
    live_interval: float = Field(10.0, ge=5, le=120, description="Seconds between polls while a favourite is playing", json_schema_extra=ADVANCED)
    idle_interval: float = Field(60.0, ge=15, le=900, description="Seconds between polls otherwise", json_schema_extra=ADVANCED)
    standings_interval: float = Field(3600.0, ge=300, description="Seconds between standings refreshes", json_schema_extra=ADVANCED)
    delay_seconds: float = Field(0.0, ge=0, le=120, description="Delay live updates to match your TV broadcast")
    show_games_within_days: int = Field(1, ge=0, le=30, description="On an off day, show the next slate only when it is this close")
    follow_spring_training: bool = Field(True, description="Treat your team's spring training games like any other game")


class MlbSource:
    key: ClassVar[str] = "mlb"
    config_model: ClassVar[type[BaseModel]] = MlbConfig

    async def run(self, ctx: SourceContext) -> None:
        api = MlbApi(ctx.http)
        await asyncio.gather(watch_logos(ctx.http, "mlb", MLB_TEAMS, ctx.log),
                             self._scores_loop(ctx, api), self._standings_loop(ctx, api))

    # -- scores + main event ------------------------------------------------

    async def _scores_loop(self, ctx: SourceContext, api: MlbApi) -> None:
        delayed: list[tuple[float, dict[str, Any] | None, list[dict[str, Any]]]] = []
        while True:
            cfg: MlbConfig = ctx.config  # type: ignore[assignment]
            if not cfg.enabled:
                await ctx.sleep(60)
                continue
            main: dict[str, Any] | None = None
            try:
                today = _today(ctx)
                end = (date.fromisoformat(today) + timedelta(days=cfg.show_games_within_days)).isoformat()
                games = normalize_schedule(await api.schedule(today, end))
                if not cfg.follow_spring_training:
                    games = [g for g in games if g["game_type"] != "S"]
                ctx.publish(sorted(games, key=lambda g: (g["date"], g["start_time_utc"])), subkey="schedule")   # the whole window
                games = _slate(games, today)
                main = select_main_event(games, cfg.favorites, today=today)
                if main and main["state"] == "LIVE":
                    main = await self._enrich(ctx, api, main)
                if main:
                    main = {**main, "favorite_side": favorite_side(main, cfg.favorites)}
                self._deliver(ctx, cfg, main, games, delayed)
            except MlbApiError as exc:
                ctx.log.warning("MLB score poll failed: %s", exc)
                main = ctx.snapshot().get("mlb.main_event") or None
            active = bool(main and main["state"] in ACTIVE_STATES and main["date"] == _today(ctx))
            await ctx.sleep(cfg.live_interval if active else cfg.idle_interval)

    async def _enrich(self, ctx: SourceContext, api: MlbApi, main: dict[str, Any]) -> dict[str, Any]:
        """Last play / pitch / no-hitter flags / decisions come only from the live feed."""
        try:
            return enrich_from_feed(main, await api.live_feed(main["id"]))
        except (MlbApiError, KeyError, TypeError, ValueError) as exc:
            ctx.log.debug("live feed failed for %s: %s", main["id"], exc)
            return main

    def _deliver(self, ctx: SourceContext, cfg: MlbConfig, main, games, delayed) -> None:
        """Publish now, or hold for ``delay_seconds`` so alerts line up with a TV broadcast."""
        if cfg.delay_seconds <= 0:
            delayed.clear()
            ctx.publish(games, subkey="scores")
            ctx.publish_to("mlb.main_event", main)
            return
        now = asyncio.get_event_loop().time()
        delayed.append((now, main, games))
        while delayed and now - delayed[0][0] >= cfg.delay_seconds:
            _, m, g = delayed.pop(0)
            ctx.publish(g, subkey="scores")
            ctx.publish_to("mlb.main_event", m)

    # -- standings + team summaries + season --------------------------------

    async def _standings_loop(self, ctx: SourceContext, api: MlbApi) -> None:
        while True:
            cfg: MlbConfig = ctx.config  # type: ignore[assignment]
            if not cfg.enabled:
                await ctx.sleep(60)
                continue
            try:
                today = _today(ctx)
                year = int(today[:4])
                standings_year = year
                raw = await api.standings(year)
                if not any(r.get("teamRecords") for r in raw.get("records") or []):
                    raw = await api.standings(year - 1)             # spring: last season's table until the opener
                    standings_year = year - 1
                standings = normalize_standings(raw)
                if standings["teams"]:
                    ctx.publish(standings, subkey="standings")
                start = (date.fromisoformat(today) - timedelta(days=SUMMARY_LOOKBACK_DAYS)).isoformat()
                end = (date.fromisoformat(today) + timedelta(days=SUMMARY_LOOKAHEAD_DAYS)).isoformat()
                summaries: dict[str, Any] = {}
                for abbrev in cfg.favorites:
                    schedule = None
                    try:
                        schedule = await api.schedule(start, end, team_id=TEAM_IDS.get(abbrev))
                    except MlbApiError as exc:
                        ctx.log.warning("MLB schedule fetch failed for %s: %s", abbrev, exc)
                    summaries[abbrev] = team_summary(abbrev, standings, schedule, today)
                ctx.publish(summaries, subkey="team_summary")
                await self._publish_season(ctx, api, cfg, today, standings_year)
            except (MlbApiError, KeyError, IndexError, ValueError) as exc:
                ctx.log.warning("MLB standings poll failed: %s", exc)
            await asyncio.sleep(cfg.standings_interval)

    async def _publish_season(self, ctx: SourceContext, api: MlbApi, cfg: MlbConfig, today: str, standings_year: int) -> None:
        try:
            payload = await api.season(int(today[:4]))
        except MlbApiError as exc:
            ctx.log.warning("MLB season info failed: %s", exc)
            return
        fav = cfg.favorites[0] if cfg.favorites else None
        info = season_info(payload, date.fromisoformat(today), None, fav, standings_year)
        opener = None
        if fav and info["phase"] in ("offseason", "preseason") and info.get("regular_start"):
            try:
                end = (date.fromisoformat(info["regular_start"]) + timedelta(days=6)).isoformat()
                opener = await api.schedule(info["regular_start"], end, team_id=TEAM_IDS.get(fav), hydrate="team")
            except MlbApiError as exc:
                ctx.log.debug("opener lookup failed for %s: %s", fav, exc)
            if opener:
                info = season_info(payload, date.fromisoformat(today), opener, fav, standings_year)
        ctx.publish({**info, "favorite": fav}, subkey="season")


def _slate(games: list[dict[str, Any]], today: str) -> list[dict[str, Any]]:
    """Today's games; on an off day, the nearest upcoming day's games (within the fetched window)."""
    todays = [g for g in games if g["date"] == today]
    if todays:
        return todays
    nearest = min((g["date"] for g in games if g["date"] > today), default=None)
    return [g for g in games if g["date"] == nearest] if nearest else []


def _today(ctx: SourceContext) -> str:
    try:
        return datetime.now(ZoneInfo(ctx.timezone)).date().isoformat() if ctx.timezone else datetime.now().astimezone().date().isoformat()
    except Exception:
        return datetime.now().astimezone().date().isoformat()
