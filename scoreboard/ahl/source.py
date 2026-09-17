"""The AHL data source: polls HockeyTech and publishes normalised data.

Snapshot keys published:
  ahl.scores        tonight's games (last night's finals stay until the rollover hour)
  ahl.schedule      games dated today .. today + show_games_within_days (the dashboard's list)
  ahl.main_event    the favourite game to show, or None (the arbiter picks the app-wide one)
  ahl.standings     normalised standings for the regular season in progress (or the last one)
  ahl.team_summary  {abbrev: summary} for each favourite
  ahl.season        phase and dates from the league's seasons list

The score bar carries every game's period, clock and score, so one request covers the slate;
the followed game's game-centre summary is fetched on top while it is live, for goals,
penalties, shots and the reconstructed power play.
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..config.models import ADVANCED
from ..data.gameday import carry_last_night, is_last_nights
from ..data.source import SourceContext
from ..logos import register_urls
from ..logos import watch as watch_logos
from ..nfl.source import _days, _today
from ..nhl.select import favorite_side, select_main_event
from .api import AhlApi, AhlApiError
from .normalize import (
    enrich_from_summary,
    normalize_scorebar,
    normalize_standings,
    pick_seasons,
    season_types,
    team_summary,
)
from .teams import AHL_TEAMS, CLUBS

TeamAbbrev = Literal[AHL_TEAMS]  # type: ignore[valid-type]
ACTIVE_STATES = frozenset({"LIVE"})
ENRICHED_STATES = frozenset({"LIVE", "OFF"})     # the summary also carries a final's shots and scorers
STANDINGS_HEAD_START = 5.0


class AhlConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="AHL")
    enabled: bool = True
    favorites: list[TeamAbbrev] = Field([], description="Favourite clubs, highest priority first", json_schema_extra={"x-widget": "team-picker"})
    live_interval: float = Field(10.0, ge=5, le=60, description="Seconds between polls while a favourite is playing", json_schema_extra=ADVANCED)
    idle_interval: float = Field(120.0, ge=30, le=600, description="Seconds between polls otherwise", json_schema_extra=ADVANCED)
    standings_interval: float = Field(3600.0, ge=300, description="Seconds between standings refreshes", json_schema_extra=ADVANCED)
    show_games_within_days: int = Field(2, ge=0, le=30, description="Only show the league slate (ticker) when it is this close; further-out games stay off the panel")
    follow_preseason: bool = Field(True, description="Treat your club's preseason games like any other game")


class AhlSource:
    key: ClassVar[str] = "ahl"
    config_model: ClassVar[type[BaseModel]] = AhlConfig
    sport: ClassVar[str] = "ahl"
    label: ClassVar[str] = "AHL"
    teams: ClassVar[tuple[str, ...]] = AHL_TEAMS

    def __init__(self) -> None:
        self._standings_ready = asyncio.Event()
        self._season_types: dict[str, int] = {}
        self._team_ids: dict[str, str] = {}
        self._logo_task: asyncio.Task | None = None
        self._checked = False

    def _api(self, ctx: SourceContext) -> AhlApi:
        return AhlApi(ctx.http)

    async def run(self, ctx: SourceContext) -> None:
        api = self._api(ctx)
        self._standings_ready = asyncio.Event()
        try:
            await asyncio.gather(self._scores_loop(ctx, api), self._standings_loop(ctx, api))
        finally:
            if self._logo_task is not None:
                self._logo_task.cancel()

    # -- scores + main event ------------------------------------------------

    async def _scores_loop(self, ctx: SourceContext, api: AhlApi) -> None:
        try:                                   # season types and logos come from the standings loop; give it a head start
            await asyncio.wait_for(self._standings_ready.wait(), timeout=STANDINGS_HEAD_START)
        except TimeoutError:
            pass
        while True:
            cfg: AhlConfig = ctx.config  # type: ignore[assignment]
            if not cfg.enabled:
                await ctx.sleep(60)
                continue
            main: dict[str, Any] | None = None
            try:
                payload = await api.scorebar(days_back=1, days_ahead=max(cfg.show_games_within_days, 1))
                games = normalize_scorebar(payload, self._season_types)
                if not cfg.follow_preseason:
                    games = [g for g in games if g["type"] != 1]
                today = _today(ctx)
                ctx.publish([g for g in games if 0 <= _days(today, g["date"]) <= cfg.show_games_within_days], subkey="schedule")
                ctx.publish(self._slate(games, today, cfg, carry_last_night(ctx)), subkey="scores")
                main = select_main_event(games, cfg.favorites, today=today)
                if main and main["state"] in ENRICHED_STATES:
                    main = await self._enrich(ctx, api, main)
                if main:
                    main = {**main, "favorite_side": favorite_side(main, cfg.favorites), "sport": "ahl"}
                ctx.publish_to("ahl.main_event", main)
            except AhlApiError as exc:
                ctx.log.warning("%s score poll failed: %s", self.label, exc)
                main = ctx.snapshot().get("ahl.main_event") or None      # keep the cadence of the last known state
            active = bool(main and main["state"] in ACTIVE_STATES)
            await ctx.sleep(cfg.live_interval if active else cfg.idle_interval)

    @staticmethod
    def _slate(games: list[dict[str, Any]], today: str, cfg: AhlConfig, carry: bool) -> list[dict[str, Any]]:
        """Tonight's games for the ticker, with last night's results ahead of them until the rollover hour;
        nothing when the next game is further out than ``show_games_within_days`` (results still show)."""
        todays = [g for g in games if g["date"] == today]
        last_night = [g for g in games if is_last_nights(g, today)] if carry else []
        upcoming = [g for g in games if g["date"] > today and g["phase"] == "pregame"]
        nearest = min((g["date"] for g in upcoming), default=None)
        if not todays and nearest and _days(today, nearest) > cfg.show_games_within_days:
            return last_night
        return [*last_night, *todays]

    async def _enrich(self, ctx: SourceContext, api: AhlApi, main: dict[str, Any]) -> dict[str, Any]:
        """Goals, penalties, shots and the power play from the game-centre summary."""
        try:
            summary = await api.game_summary(main["id"])
        except AhlApiError as exc:
            ctx.log.debug("game summary fetch failed for %s: %s", main["id"], exc)
            return main
        try:
            return enrich_from_summary(main, summary)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            ctx.drift(f"the game summary could not be normalised ({type(exc).__name__}: {exc}); showing the score bar only")
            return main

    # -- standings, seasons, team summaries ---------------------------------

    async def _standings_loop(self, ctx: SourceContext, api: AhlApi) -> None:
        while True:
            cfg: AhlConfig = ctx.config  # type: ignore[assignment]
            if not cfg.enabled:
                self._standings_ready.set()
                await ctx.nap(60)
                continue
            try:
                today = _today(ctx)
                seasons = await api.seasons()
                self._season_types = season_types(seasons)
                picked = pick_seasons(seasons, date.fromisoformat(today))
                await self._refresh_teams(ctx, api, picked["schedule_season_id"])
                standings: dict[str, Any] = {}
                if picked["standings_season_id"]:
                    standings = normalize_standings(await api.standings(picked["standings_season_id"]))
                ctx.publish(standings, subkey="standings")
                self._standings_ready.set()
                summaries: dict[str, Any] = {}
                first_game = None
                for abbrev in cfg.favorites:
                    schedule = None
                    if abbrev in self._team_ids and picked["schedule_season_id"]:
                        try:
                            schedule = await api.schedule(picked["schedule_season_id"], self._team_ids[abbrev])
                        except AhlApiError as exc:
                            ctx.log.warning("%s schedule fetch failed for %s: %s", self.label, abbrev, exc)
                    summaries[abbrev] = team_summary(abbrev, standings, schedule, today)
                    if first_game is None and abbrev == (cfg.favorites[0] if cfg.favorites else None):
                        first_game = _first_game(summaries[abbrev], today)
                ctx.publish(summaries, subkey="team_summary")
                fav = cfg.favorites[0] if cfg.favorites else None
                ctx.publish({**picked["info"], "favorite": fav, "first_game": first_game}, subkey="season")
            except (AhlApiError, KeyError, IndexError, ValueError) as exc:
                ctx.log.warning("%s standings poll failed: %s", self.label, exc)
                self._standings_ready.set()
            await ctx.nap(cfg.standings_interval)

    async def _refresh_teams(self, ctx: SourceContext, api: AhlApi, season_id: int | None) -> None:
        """The league's team list: ids for the schedule calls, logo URLs for the cache, and a realignment check."""
        payload = await api.teams(season_id)
        rows = payload.get("Teamsbyseason") or []
        if not rows:
            return
        ids, urls = {}, {}
        for t in rows:
            code = str(t.get("code") or "").upper()
            if code:
                ids[code] = str(t.get("id") or "")
                urls[code] = str(t.get("team_logo_url") or "")
        self._team_ids = ids
        register_urls("ahl", urls)
        if self._logo_task is None or self._logo_task.done():
            self._logo_task = asyncio.create_task(watch_logos(ctx.http, "ahl", tuple(sorted(set(ids) | set(AHL_TEAMS))), ctx.log), name="logos:ahl")
        if not self._checked:
            self._checked = True
            stale = sorted(set(CLUBS) - set(ids))
            unknown = sorted(set(ids) - set(CLUBS))
            if stale:
                ctx.log.warning("AHL: the league's team list does not carry %s — update scoreboard/ahl/teams.py", ", ".join(stale))
            if unknown:
                ctx.log.info("AHL: clubs not in the registry (neutral colours, cannot be favourites yet): %s", ", ".join(unknown))


def _first_game(summary: dict[str, Any], today: str) -> dict[str, Any] | None:
    """The favourite's next game, in the shape the season countdown board reads."""
    nxt = summary.get("next_game")
    if not nxt or not nxt.get("date") or nxt["date"] < today:
        return None
    return {"date": nxt["date"], "home": nxt["home"], "opponent": nxt["opponent"], "start_time_utc": nxt.get("start_time_utc", "")}


def poll_active(main: dict[str, Any] | None) -> bool:
    return bool(main and main.get("state") in ACTIVE_STATES)
