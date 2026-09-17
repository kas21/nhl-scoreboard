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
import re
from datetime import date, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config.models import ADVANCED
from ..data.gameday import carry_last_night, is_last_nights, yesterday
from ..data.source import SourceContext
from ..logos import watch as watch_logos
from .api import NhlApi, NhlApiError
from .contract import check_landing, check_score_payload, check_standings_payload
from .normalize import (
    ACTIVE_STATES,
    normalize_game,
    normalize_standings,
    records_from_standings,
    team_summary,
)
from .schedule import fetch_weeks, schedule_games
from .season import season_info
from .select import favorite_side, select_main_event
from .teams import NHL_TEAMS

log = logging.getLogger(__name__)

OFFLINE_AFTER_FAILURES = 3      # consecutive score-poll failures before we report offline
ABBREV = re.compile(r"[A-Z]{2,4}")   # what an NHL team code looks like; the registry is not the last word on which exist
NORMALISE_ERRORS = (KeyError, TypeError, ValueError, AttributeError)   # a game the feed shaped in a way normalize cannot read


class NhlConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="NHL")
    # Validated as a code, not against the registry: a relocated or expansion team can be followed
    # before teams.py catches up (the source reports the mismatch as feed drift). The picker still
    # offers the registry, via the schema's enum.
    favorites: list[str] = Field(["TOR"], description="Favourite teams, highest priority first",
                                 json_schema_extra={"x-widget": "team-picker", "items": {"type": "string", "enum": list(NHL_TEAMS)}})
    live_interval: float = Field(5.0, ge=2, le=60, description="Seconds between polls while a favourite is playing", json_schema_extra=ADVANCED)
    idle_interval: float = Field(60.0, ge=15, le=600, description="Seconds between polls otherwise", json_schema_extra=ADVANCED)
    standings_interval: float = Field(3600.0, ge=300, description="Seconds between standings refreshes", json_schema_extra=ADVANCED)
    delay_seconds: float = Field(0.0, ge=0, le=120, description="Delay live updates to match your TV broadcast")
    show_games_within_days: int = Field(2, ge=0, le=30, description="Only show the league slate (ticker) when it is this close; further-out games stay off the panel")
    follow_preseason: bool = Field(True, description="Treat your team's preseason games like any other game")

    @field_validator("favorites", mode="before")
    @classmethod
    def _upper_codes(cls, value: Any) -> Any:
        return [str(v).strip().upper() for v in value] if isinstance(value, list) else value

    @field_validator("favorites")
    @classmethod
    def _team_codes(cls, value: list[str]) -> list[str]:
        for code in value:
            if not ABBREV.fullmatch(code):
                raise ValueError(f"{code!r} is not a team abbreviation (two to four letters, like TOR)")
        return value


class NhlSource:
    key: ClassVar[str] = "nhl"
    config_model: ClassVar[type[BaseModel]] = NhlConfig

    def __init__(self) -> None:
        self._standings_ready = asyncio.Event()
        self._held_polls = 0        # polls the main event has been held back waiting for a goal's details

    async def run(self, ctx: SourceContext) -> None:
        api = NhlApi(ctx.http)
        self._standings_ready = asyncio.Event()
        cfg: NhlConfig = ctx.config  # type: ignore[assignment]
        logo_teams = tuple(dict.fromkeys((*NHL_TEAMS, *cfg.favorites)))    # a favourite outside the registry still gets its logo
        # A task group, not gather: gather leaves the other loops running when one raises,
        # and the supervisor's restart of run() then starts a second set beside them.
        async with asyncio.TaskGroup() as tg:
            tg.create_task(watch_logos(ctx.http, "nhl", logo_teams, ctx.log))
            tg.create_task(self._scores_loop(ctx, api))
            tg.create_task(self._standings_loop(ctx, api))

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
                _report(ctx, check_score_payload(payload))
                records = records_from_standings(ctx.snapshot().get("nhl.standings"))
                games = _normalize_games(ctx, payload.get("games") or [], records)
                games = _followed(games, cfg)
                today = _local_today(ctx)
                slate_date = payload.get("currentDate") or (games[0]["date"] if games else today)
                if _days_between(today, slate_date) > cfg.show_games_within_days:
                    games = []                                   # too far out to be "tonight's games"
                if carry_last_night(ctx):
                    games = await self._with_last_night(ctx, api, cfg, games, slate_date, today, records)
                main = select_main_event(games, cfg.favorites, today=today, timezone=ctx.timezone)
                if main and main["state"] in ACTIVE_STATES:
                    main = await self._enrich(ctx, api, main, records)
                if main:
                    main = {**main, "favorite_side": favorite_side(main, cfg.favorites), "sport": "nhl"}
                main = self._wait_for_scorer(ctx, main)
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
            await ctx.sleep(cfg.live_interval if active else cfg.idle_interval)

    async def _with_last_night(self, ctx: SourceContext, api: NhlApi, cfg: NhlConfig, games: list[dict[str, Any]],
                               slate_date: str, today: str, records: dict[str, str]) -> list[dict[str, Any]]:
        """Before the game-day rollover hour the ticker shows last night's results ahead of today's games.

        ``/score/now`` covers one league day and turns on the league's own schedule, so whichever of
        the two days it left out is fetched by date. The main event is still picked by today's date,
        so a carried-over final never brings the postgame board back.
        """
        last = yesterday(today)
        missing = today if slate_date == last else last
        try:
            payload = await api.score(missing)
        except NhlApiError as exc:
            ctx.log.debug("score fetch for %s failed, ticker shows the league day only: %s", missing, exc)
            return games
        extra = _followed([g for g in _normalize_games(ctx, payload.get("games") or [], records) if g["date"] == missing], cfg)
        if missing == today:                                     # the league is still on last night: keep its results, add today
            return [*(g for g in games if is_last_nights(g, today)), *extra]
        return [*(g for g in extra if is_last_nights(g, today)), *games]

    def _wait_for_scorer(self, ctx: SourceContext, main: dict[str, Any] | None) -> dict[str, Any] | None:
        """Hold a score change back for one poll when the goal behind it has no details yet.

        The landing feed's score increments a poll or so before its scoring summary lists the
        goal, so a goal published the moment the score moved carried no scorer, the card was
        skipped, and the scorer never showed because the score did not change again. One poll
        (five seconds, live) with the previous main event still published is what it costs to
        celebrate with a name. Bounded: a second poll publishes whatever there is (a shootout
        goal never gets an entry, and a feed can simply be late).
        """
        previous = ctx.snapshot().get("nhl.main_event")
        if not main or not previous or previous.get("id") != main.get("id") or main.get("state") not in ACTIVE_STATES:
            self._held_polls = 0
            return main
        for side in ("away", "home"):
            scored = main[side]["score"] - previous[side]["score"]
            listed = sum(1 for g in main.get("goals") or [] if g.get("team") == main[side]["abbrev"])
            listed_before = sum(1 for g in previous.get("goals") or [] if g.get("team") == previous[side]["abbrev"])
            if scored > 0 and listed - listed_before < scored and self._held_polls < 1:
                self._held_polls += 1
                ctx.log.debug("holding a %s goal one poll for the scorer", main[side]["abbrev"])
                return previous
        self._held_polls = 0
        return main

    async def _enrich(self, ctx: SourceContext, api: NhlApi, main: dict[str, Any], records: dict[str, str]) -> dict[str, Any]:
        """Add situation (power play / pulled goalie) and penalties from the landing feed."""
        try:
            landing = await api.landing(main["id"])
        except NhlApiError as exc:
            log.debug("landing fetch failed for %s: %s", main["id"], exc)
            return _carry_landing(main, ctx.snapshot().get("nhl.main_event"))
        _report(ctx, check_landing(landing))
        try:
            return normalize_game(_score_shape(main, landing), records, landing)
        except NORMALISE_ERRORS as exc:
            ctx.drift(f"the landing feed could not be normalised ({type(exc).__name__}: {exc}); showing the score feed only")
            return main

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
                _report(ctx, check_standings_payload(raw_standings))
                standings = normalize_standings(raw_standings)
                _check_teams(ctx, cfg, standings)
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
                    weeks = await fetch_weeks(api, sched_now, today, cfg.show_games_within_days)
                    ctx.publish(schedule_games(weeks, records_from_standings(standings), today, cfg.show_games_within_days, cfg.follow_preseason),
                                subkey="schedule")
                except (NhlApiError, ValueError) as exc:
                    ctx.log.warning("season info failed: %s", exc)
            except NhlApiError as exc:
                ctx.log.warning("standings poll failed: %s", exc)
                self._standings_ready.set()
            await ctx.nap(cfg.standings_interval)


def _carry_landing(main: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """The score feed alone has no penalties, power play or goal details. While the landing
    feed is unreachable, keep the last ones for the same game rather than publishing them as
    gone: an empty penalty list followed by the full one replayed every penalty alert, and
    the power-play chip blinked off and on."""
    if not previous or previous.get("id") != main.get("id"):
        return main
    carried = {k: previous[k] for k in ("penalties", "powerplay", "pulled_goalie", "goals") if k in previous}
    return {**main, **carried}


def _report(ctx: SourceContext, notes: list[str]) -> None:
    for note in notes:
        ctx.drift(note)


def _followed(games: list[dict[str, Any]], cfg: NhlConfig) -> list[dict[str, Any]]:
    return games if cfg.follow_preseason else [g for g in games if g["type"] != 1]


def _normalize_games(ctx: SourceContext, raws: list[dict[str, Any]], records: dict[str, str]) -> list[dict[str, Any]]:
    """Normalise the slate one game at a time: a game the feed shaped oddly is dropped and reported,
    the rest of the night carries on (the whole source restarting would take standings down too)."""
    games = []
    for raw in raws:
        try:
            games.append(normalize_game(raw, records))
        except NORMALISE_ERRORS as exc:
            ctx.drift(f"a score game could not be normalised and was skipped ({type(exc).__name__}: {exc})")
    return games


def _check_teams(ctx: SourceContext, cfg: NhlConfig, standings: dict[str, Any]) -> None:
    """The registry against the league: a team the standings list but teams.py does not is a
    relocation or expansion we have not caught up with; a favourite the standings do not list is
    a code the league does not use (both are accepted — the panel shows neutral colours and no logo
    until teams.py is updated)."""
    listed = set(standings.get("teams") or {})
    if not listed:
        return
    for abbrev in sorted(listed - set(NHL_TEAMS)):
        ctx.drift(f"the standings list {abbrev}, which is not in scoreboard/nhl/teams.py — add it for colours and a name")
    for abbrev in cfg.favorites:
        if abbrev not in listed:
            ctx.drift(f"favourite {abbrev} is not in the NHL standings — is the code right?")


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
