"""NFL data source (ESPN). Publishes nfl.scores, nfl.main_event, nfl.standings, nfl.team_summary.

The loops are written against class-level hooks (``sport``, ``teams``, the normalisers and
``_slate``) so another ESPN football league — college — is a subclass, not a copy.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, ClassVar, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from ..config.models import ADVANCED
from ..data.source import SourceContext
from ..logos import watch as watch_logos
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
    live_interval: float = Field(20.0, ge=10, le=120, description="Seconds between polls while a favourite is playing", json_schema_extra=ADVANCED)
    idle_interval: float = Field(300.0, ge=60, le=3600, json_schema_extra=ADVANCED)
    standings_interval: float = Field(3600.0, ge=600, json_schema_extra=ADVANCED)
    show_games_within_days: int = Field(2, ge=0, le=30, description="Only show the week's slate when the next game is this close")


class NflSource:
    key: ClassVar[str] = "nfl"
    config_model: ClassVar[type[BaseModel]] = NflConfig
    sport: ClassVar[str] = "nfl"
    label: ClassVar[str] = "NFL"
    teams: ClassVar[tuple[str, ...]] = NFL_TEAMS

    # -- hooks a sibling league overrides ------------------------------------

    def _api(self, ctx: SourceContext) -> NflApi:
        return NflApi(ctx.http)

    def _scoreboard(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return normalize_scoreboard(payload)

    def _standings(self, payload: dict[str, Any]) -> dict[str, Any]:
        return normalize_standings(payload)

    def _summary(self, abbrev: str, standings: dict[str, Any], schedule: dict[str, Any] | None, today: str) -> dict[str, Any]:
        return team_summary(abbrev, standings, schedule, today)

    def _slate(self, games: list[dict[str, Any]], cfg: BaseModel) -> list[dict[str, Any]]:
        """The games worth showing on the ticker and dashboard (the main event is picked from all of them)."""
        return games

    def _check_teams(self, ctx: SourceContext, listed: dict[str, str]) -> None:
        """Called with ESPN's abbreviation -> id map once per standings refresh."""

    # -- loops ----------------------------------------------------------------

    async def run(self, ctx: SourceContext) -> None:
        api = self._api(ctx)
        await asyncio.gather(watch_logos(ctx.http, self.sport, self.teams, ctx.log),
                             self._scores_loop(ctx, api), self._standings_loop(ctx, api))

    async def _scores_loop(self, ctx: SourceContext, api: NflApi) -> None:
        while True:
            cfg: NflConfig = ctx.config  # type: ignore[assignment]
            if not cfg.enabled:
                await ctx.sleep(60)
                continue
            main = None
            try:
                games = self._scoreboard(await api.scoreboard())      # current week
                today = _today(ctx)
                games = sorted(games, key=lambda g: g["start_time_utc"])
                ctx.publish(_season(games, today, self.sport), subkey="season")
                slate = self._slate(games, cfg)
                ctx.publish([g for g in slate if 0 <= _days(today, g["date"]) <= cfg.show_games_within_days], subkey="schedule")
                upcoming = [g for g in games if g["phase"] != "postgame"]
                nearest = min((g["date"] for g in upcoming), default=None)
                if nearest and _days(today, nearest) > cfg.show_games_within_days and not any(g["phase"] in ("live", "intermission") for g in games):
                    slate = [g for g in slate if g["phase"] == "postgame"]        # keep results, hide far-off games
                main = select_main_event(games, cfg.favorites, today=today)
                if main:
                    main = {**main, "favorite_side": favorite_side(main, cfg.favorites)}
                ctx.publish(slate, subkey="scores")
                ctx.publish_to(f"{self.sport}.main_event", main)
            except NflApiError as exc:
                ctx.log.warning("%s score poll failed: %s", self.label, exc)
            active = bool(main and main["phase"] in ("live", "intermission", "pregame") and main["date"] == _today(ctx))
            await ctx.sleep(cfg.live_interval if active else cfg.idle_interval)

    async def _standings_loop(self, ctx: SourceContext, api: NflApi) -> None:
        while True:
            cfg: NflConfig = ctx.config  # type: ignore[assignment]
            if not cfg.enabled:
                await ctx.sleep(60)
                continue
            try:
                standings = self._standings(await api.standings())
                ctx.publish(standings, subkey="standings")
                teams = (await api.teams())["sports"][0]["leagues"][0]["teams"]
                ids = {t["team"]["abbreviation"]: t["team"]["id"] for t in teams}
                self._check_teams(ctx, ids)
                summaries: dict[str, Any] = {}
                for abbrev in cfg.favorites:
                    schedule = None
                    if abbrev in ids:
                        try:
                            schedule = await api.team_schedule(ids[abbrev])
                        except NflApiError as exc:
                            ctx.log.warning("%s schedule fetch failed for %s: %s", self.label, abbrev, exc)
                    summaries[abbrev] = self._summary(abbrev, standings, schedule, _today(ctx))
                ctx.publish(summaries, subkey="team_summary")
            except (NflApiError, KeyError, IndexError) as exc:
                ctx.log.warning("%s standings poll failed: %s", self.label, exc)
            await asyncio.sleep(cfg.standings_interval)


def _today(ctx: SourceContext) -> str:
    try:
        return datetime.now(ZoneInfo(ctx.timezone)).date().isoformat() if ctx.timezone else datetime.now().astimezone().date().isoformat()
    except Exception:
        return datetime.now().astimezone().date().isoformat()


def _days(today: str, other: str) -> int:
    from datetime import date as _d
    try:
        return (_d.fromisoformat(other) - _d.fromisoformat(today)).days
    except ValueError:
        return 0


def _season(games: list, today: str, sport: str = "nfl") -> dict:
    """Phase from the slate ESPN hands us: no games = offseason; season.type 1/2/3 = pre/regular/playoffs."""
    if not games:
        return {"sport": sport, "phase": "offseason", "week": None, "next_game_date": None, "days_to_next": None}
    types = {g["type"] for g in games}
    phase = "playoffs" if 3 in types else "regular" if 2 in types else "preseason"
    nxt = min((g for g in games if g["phase"] != "postgame"), key=lambda g: g["start_time_utc"], default=None)
    return {"sport": sport, "phase": phase, "week": games[0].get("week"),
            "next_game_date": nxt["date"] if nxt else None, "days_to_next": _days(today, nxt["date"]) if nxt else None}
