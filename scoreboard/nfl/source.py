"""NFL data source (ESPN). Publishes nfl.scores, nfl.main_event, nfl.standings, nfl.team_summary."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, ClassVar, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from ..data.source import SourceContext
from ..nhl.select import favorite_side, select_main_event
from .api import NflApi, NflApiError
from .normalize import normalize_scoreboard, normalize_standings, team_summary
from .teams import NFL_TEAMS

TeamAbbrev = Literal[NFL_TEAMS]  # type: ignore[valid-type]
OFFLINE_AFTER_FAILURES = 3


class NflConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="NFL")
    enabled: bool = True
    favorites: list[TeamAbbrev] = Field([], description="Favourite teams, highest priority first", json_schema_extra={"x-widget": "team-picker"})
    live_interval: float = Field(20.0, ge=10, le=120, description="Seconds between polls while a favourite is playing")
    idle_interval: float = Field(300.0, ge=60, le=3600)
    standings_interval: float = Field(3600.0, ge=600)


class NflSource:
    key: ClassVar[str] = "nfl"
    config_model: ClassVar[type[BaseModel]] = NflConfig

    async def run(self, ctx: SourceContext) -> None:
        api = NflApi(ctx.http)
        await asyncio.gather(self._scores_loop(ctx, api), self._standings_loop(ctx, api))

    async def _scores_loop(self, ctx: SourceContext, api: NflApi) -> None:
        while True:
            cfg: NflConfig = ctx.config  # type: ignore[assignment]
            if not cfg.enabled:
                await asyncio.sleep(60)
                continue
            main = None
            try:
                games = normalize_scoreboard(await api.scoreboard())      # current week
                today = _today(ctx)
                games = sorted(games, key=lambda g: g["start_time_utc"])
                main = select_main_event(games, cfg.favorites, today=today)
                if main:
                    main = {**main, "favorite_side": favorite_side(main, cfg.favorites)}
                ctx.publish(games, subkey="scores")
                ctx.publish_to("nfl.main_event", main)
            except NflApiError as exc:
                ctx.log.warning("NFL score poll failed: %s", exc)
            active = bool(main and main["phase"] in ("live", "intermission", "pregame") and main["date"] == _today(ctx))
            await asyncio.sleep(cfg.live_interval if active else cfg.idle_interval)

    async def _standings_loop(self, ctx: SourceContext, api: NflApi) -> None:
        while True:
            cfg: NflConfig = ctx.config  # type: ignore[assignment]
            if not cfg.enabled:
                await asyncio.sleep(60)
                continue
            try:
                standings = normalize_standings(await api.standings())
                ctx.publish(standings, subkey="standings")
                teams = (await api.teams())["sports"][0]["leagues"][0]["teams"]
                ids = {t["team"]["abbreviation"]: t["team"]["id"] for t in teams}
                summaries: dict[str, Any] = {}
                for abbrev in cfg.favorites:
                    schedule = None
                    if abbrev in ids:
                        try:
                            schedule = await api.team_schedule(ids[abbrev])
                        except NflApiError as exc:
                            ctx.log.warning("NFL schedule fetch failed for %s: %s", abbrev, exc)
                    summaries[abbrev] = team_summary(abbrev, standings, schedule, _today(ctx))
                ctx.publish(summaries, subkey="team_summary")
            except (NflApiError, KeyError, IndexError) as exc:
                ctx.log.warning("NFL standings poll failed: %s", exc)
            await asyncio.sleep(cfg.standings_interval)


def _today(ctx: SourceContext) -> str:
    try:
        return datetime.now(ZoneInfo(ctx.timezone)).date().isoformat() if ctx.timezone else datetime.now().astimezone().date().isoformat()
    except Exception:
        return datetime.now().astimezone().date().isoformat()
