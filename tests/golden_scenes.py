"""The scenes the golden-frame suite renders: every board, in its key states, from fixture data.

A scene is one board + config + snapshot + clock, frozen at one ``elapsed``. The harness in
``test_golden.py`` renders each scene at each of its sizes and compares the frame, pixel for
pixel, with the PNG checked in under ``tests/golden/``.

Keep scenes deterministic: everything a board can see comes from here (``ctx.now``, the
snapshot, ``elapsed``), and the one board that draws dice (splash) is seeded by the harness.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scoreboard.boards.base import BaseBoard, BoardContext, EmptyConfig
from scoreboard.boards.blank import BlankBoard
from scoreboard.boards.clock import ClockBoard, ClockConfig
from scoreboard.boards.season_countdown import CountdownConfig as SeasonCountdownConfig
from scoreboard.boards.season_countdown import SeasonCountdownBoard
from scoreboard.boards.splash import SplashBoard, SplashConfig
from scoreboard.boards.test_pattern import TestPatternBoard
from scoreboard.data import Event, Snapshot, SnapshotStore
from scoreboard.extras.flights.board import NearbyBoard, NearbyConfig, OverheadBoard, OverheadConfig
from scoreboard.extras.flights.source import normalize_aircraft, parse_adsbdb
from scoreboard.extras.holidays.board import CountdownBoard as HolidayBoard
from scoreboard.extras.holidays.board import CountdownConfig as HolidayConfig
from scoreboard.extras.holidays.images import IMAGES as HOLIDAY_IMAGES
from scoreboard.extras.weather.board import WeatherBoard, WeatherBoardConfig
from scoreboard.extras.weather.source import WeatherConfig
from scoreboard.extras.weather.source import normalize as normalize_weather
from scoreboard.mlb.boards.game import MlbGameBoard, MlbGameConfig
from scoreboard.mlb.boards.others import (
    MlbScoreBoard,
    MlbStandingsBoard,
    MlbTeamSummaryBoard,
    MlbTickerBoard,
)
from scoreboard.mlb.boards.others import ScoreConfig as MlbScoreConfig
from scoreboard.mlb.normalize import enrich_from_feed
from scoreboard.mlb.normalize import normalize_schedule as mlb_schedule
from scoreboard.mlb.normalize import normalize_standings as mlb_standings
from scoreboard.mlb.normalize import team_summary as mlb_team_summary
from scoreboard.ncaaf.boards.game import NcaafGameBoard, NcaafGameConfig
from scoreboard.ncaaf.boards.others import (
    NcaafScoreBoard,
    NcaafScoreConfig,
    NcaafStandingsBoard,
    NcaafStandingsConfig,
    NcaafTeamSummaryBoard,
    NcaafTickerBoard,
)
from scoreboard.ncaaf.normalize import normalize_scoreboard as ncaaf_scoreboard
from scoreboard.ncaaf.normalize import normalize_standings as ncaaf_standings
from scoreboard.ncaaf.normalize import team_summary as ncaaf_team_summary
from scoreboard.nfl.boards.game import NflGameBoard, NflGameConfig
from scoreboard.nfl.boards.others import (
    NflScoreBoard,
    NflStandingsBoard,
    NflTeamSummaryBoard,
    NflTickerBoard,
    ScoreConfig,
)
from scoreboard.nfl.normalize import normalize_scoreboard as nfl_scoreboard
from scoreboard.nfl.normalize import normalize_standings as nfl_standings
from scoreboard.nfl.normalize import team_summary as nfl_team_summary
from scoreboard.nhl.boards.events import GoalBoard, GoalConfig, PenaltyBoard, PenaltyConfig
from scoreboard.nhl.boards.game import GameBoard, GameConfig
from scoreboard.nhl.boards.standings import StandingsBoard, StandingsConfig
from scoreboard.nhl.boards.team_summary import TeamSummaryBoard, TeamSummaryConfig
from scoreboard.nhl.boards.ticker import TickerBoard, TickerConfig
from scoreboard.nhl.normalize import (
    normalize_game,
    normalize_standings,
    records_from_standings,
    team_summary,
)
from scoreboard.nhl.season import season_info
from scoreboard.render.profiles import PROFILES, profile_for

FIXTURES = Path(__file__).parent / "fixtures"
ALL_SIZES: tuple[tuple[int, int], ...] = tuple((p.width, p.height) for p in PROFILES)
# The panels people actually own; the flagship game boards are pinned at every profile.
COMMON_SIZES: tuple[tuple[int, int], ...] = ((128, 64), (64, 32), (128, 32))
TORONTO = ZoneInfo("America/Toronto")


@dataclass(frozen=True)
class Scene:
    name: str                      # "<board key>/<state>" -> file name under tests/golden/
    board: BaseBoard
    cfg: Any
    snapshot: Snapshot
    now: datetime
    elapsed: float
    event: Event | None = None
    sizes: tuple[tuple[int, int], ...] = COMMON_SIZES

    def context(self, width: int, height: int) -> BoardContext:
        return BoardContext(snapshot=self.snapshot, profile=profile_for(width, height), width=width,
                            height=height, fps=30, now=self.now, elapsed=self.elapsed, event=self.event)


def _load(*parts: str) -> Any:
    return json.loads(FIXTURES.joinpath(*parts).read_text())


# -- NHL ------------------------------------------------------------------------


def _nhl_world() -> dict[str, Any]:
    score = _load("nhl", "score_2026-04-11.json")
    standings = normalize_standings(_load("nhl", "standings_2026-04-10.json"))
    recs = records_from_standings(standings)
    raw = next(g for g in score["games"] if g["homeTeam"]["abbrev"] == "TOR")
    final = {**normalize_game(raw, recs, _load("nhl", "landing_2025021270.json")), "favorite_side": "home"}
    live = {**final, "state": "LIVE", "phase": "live", "clock": "12:34", "period": "2nd", "outcome": "",
            "powerplay": {"code": "h54", "clock": "01:12"}, "pulled_goalie": 1}
    pre = {**final, "state": "FUT", "phase": "pregame", "outcome": "", "start_time_utc": "2026-04-11T23:00:00Z"}
    store = SnapshotStore()
    store.publish("nhl.scores", [normalize_game(g, recs) for g in score["games"]])
    store.publish("nhl.standings", standings)
    store.publish("nhl.team_summary",
                  {"TOR": team_summary("TOR", standings, _load("nhl", "club_schedule_TOR_week.json"), "2026-04-11")})
    return {"store": store, "final": final, "live": live, "pre": pre}


def nhl_scenes() -> list[Scene]:
    w = _nhl_world()
    now = datetime(2026, 4, 11, 18, 30, tzinfo=TORONTO)
    store: SnapshotStore = w["store"]
    idle = store.get()

    def with_game(phase: str) -> Snapshot:
        return store.publish("main_event", w[phase])

    live = w["live"]
    fav_goal = Event("nhl.goal", team="TOR", payload={"side": "home", "game": live, "score": "1-3", "goal": live["goals"][-1]})
    opp_goal = Event("nhl.goal", team="FLA", payload={"side": "away", "game": live, "score": "2-3"})
    penalty = Event("nhl.penalty", team="TOR", payload={"penalty": w["final"]["penalties"][0], "game": live})
    goal_cfg = GoalConfig()
    return [
        Scene("nhl.game/pregame", GameBoard(), GameConfig(), with_game("pre"), now, 2.0, sizes=ALL_SIZES),
        Scene("nhl.game/live", GameBoard(), GameConfig(), with_game("live"), now, 3.0, sizes=ALL_SIZES),
        Scene("nhl.game/final", GameBoard(), GameConfig(), with_game("final"), now, 2.0, sizes=ALL_SIZES),
        Scene("nhl.ticker/idle", TickerBoard(), TickerConfig(), idle, now, 1.0),
        Scene("nhl.standings/idle", StandingsBoard(), StandingsConfig(), idle, now, 3.0),
        Scene("nhl.team_summary/idle", TeamSummaryBoard(), TeamSummaryConfig(), idle, now, 2.0),
        Scene("nhl.goal/favorite", GoalBoard(), goal_cfg, with_game("live"), now, 2.0, event=fav_goal),
        Scene("nhl.goal/summary", GoalBoard(), goal_cfg, with_game("live"), now, goal_cfg.duration + 1.0, event=fav_goal),
        Scene("nhl.goal/opponent", GoalBoard(), goal_cfg, with_game("live"), now, 0.2, event=opp_goal, sizes=((128, 64),)),
        Scene("nhl.penalty/live", PenaltyBoard(), PenaltyConfig(), with_game("live"), now, 1.0, event=penalty),
    ]


# -- NFL ------------------------------------------------------------------------


def nfl_scenes() -> list[Scene]:
    games = nfl_scoreboard(_load("nfl", "espn_scoreboard.json"))
    st = nfl_standings(_load("nfl", "espn_standings.json"))
    live = {**games[0], "state": "LIVE", "phase": "live", "period": "3rd", "clock": "7:12", "outcome": "",
            "favorite_side": "home",
            "situation": {"possession": "home", "down": 2, "distance": 7, "red_zone": True, "text": "2nd & 7", "last_play": ""}}
    live = {**live, "home": {**live["home"], "timeouts": 2}}
    store = SnapshotStore()
    store.publish("nfl.scores", games)
    store.publish("nfl.standings", st)
    store.publish("nfl.team_summary", {"BUF": nfl_team_summary("BUF", st, _load("nfl", "espn_schedule_BUF.json"), "2026-08-26")})
    snap = store.publish("main_event", live)
    now = datetime(2026, 8, 26, 13, tzinfo=TORONTO)
    td = Event("nfl.touchdown", team=live["home"]["abbrev"], payload={"side": "home", "game": live, "score": "7-0", "points": 7})
    fg = Event("nfl.field_goal", team=live["away"]["abbrev"], payload={"side": "away", "game": live, "score": "7-3", "points": 3})
    return [
        Scene("nfl.game/live", NflGameBoard(), NflGameConfig(), snap, now, 2.0, sizes=ALL_SIZES),
        Scene("nfl.ticker/live", NflTickerBoard(), TickerConfig(), snap, now, 2.0),
        Scene("nfl.standings/live", NflStandingsBoard(), StandingsConfig(), snap, now, 2.0),
        Scene("nfl.team_summary/live", NflTeamSummaryBoard(), TeamSummaryConfig(), snap, now, 2.0),
        Scene("nfl.score/touchdown", NflScoreBoard(), ScoreConfig(), snap, now, 1.0, event=td),
        Scene("nfl.score/field_goal", NflScoreBoard(), ScoreConfig(), snap, now, 1.0, event=fg, sizes=((128, 64),)),
    ]


# -- College football -------------------------------------------------------------


def ncaaf_scenes() -> list[Scene]:
    games = ncaaf_scoreboard(_load("ncaaf", "espn_scoreboard.json"))
    st = ncaaf_standings(_load("ncaaf", "espn_standings.json"))
    live = {**games[2], "favorite_side": "away"}                      # #8 MICH @ #20 OU, 3rd quarter, red zone
    store = SnapshotStore()
    store.publish("ncaaf.scores", games)
    store.publish("ncaaf.standings", st)
    store.publish("ncaaf.team_summary", {"MICH": ncaaf_team_summary("MICH", st, _load("ncaaf", "espn_schedule_MICH.json"), "2026-09-05")})
    snap = store.publish("main_event", live)
    now = datetime(2026, 9, 5, 12, tzinfo=TORONTO)
    td = Event("ncaaf.touchdown", team="MICH", payload={"side": "away", "game": live, "score": "21-10", "points": 7})
    fg = Event("ncaaf.field_goal", team="OU", payload={"side": "home", "game": live, "score": "21-13", "points": 3})
    return [
        Scene("ncaaf.game/live", NcaafGameBoard(), NcaafGameConfig(), snap, now, 2.0, sizes=ALL_SIZES),
        Scene("ncaaf.ticker/live", NcaafTickerBoard(), TickerConfig(), snap, now, 2.0),
        Scene("ncaaf.standings/live", NcaafStandingsBoard(), NcaafStandingsConfig(), snap, now, 2.0),
        Scene("ncaaf.team_summary/live", NcaafTeamSummaryBoard(), TeamSummaryConfig(), snap, now, 2.0),
        Scene("ncaaf.score/touchdown", NcaafScoreBoard(), NcaafScoreConfig(), snap, now, 1.0, event=td),
        Scene("ncaaf.score/field_goal", NcaafScoreBoard(), NcaafScoreConfig(), snap, now, 1.0, event=fg, sizes=((128, 64),)),
    ]


# -- MLB ------------------------------------------------------------------------


def mlb_scenes() -> list[Scene]:
    """Fixtures are generated in the Stats API's shape (tests/fixtures/mlb/README.md), the 2026-09-03 slate."""
    games = mlb_schedule(_load("mlb", "schedule_2026-09-03.json"))
    st = mlb_standings(_load("mlb", "standings_2026.json"))

    def by_id(pk: int) -> dict[str, Any]:
        return next(g for g in games if g["id"] == str(pk))

    live = {**enrich_from_feed(by_id(776002), _load("mlb", "feed_live_776002.json")), "favorite_side": "away"}
    final = {**enrich_from_feed(by_id(776001), _load("mlb", "feed_live_776001.json")), "favorite_side": "away"}
    pregame = {**by_id(776003), "favorite_side": "away"}
    inning_break = {**by_id(776006), "favorite_side": "home"}
    store = SnapshotStore()
    store.publish("mlb.scores", games)
    store.publish("mlb.standings", st)
    store.publish("mlb.season", {"sport": "mlb", "phase": "regular", "standings_final": False})
    store.publish("mlb.team_summary",
                  {"NYY": mlb_team_summary("NYY", st, _load("mlb", "schedule_NYY_2026-08-24_2026-09-17.json"), "2026-09-03")})

    def with_game(game: dict[str, Any]) -> Snapshot:
        return store.publish("main_event", game)

    now = datetime(2026, 9, 3, 19, tzinfo=ZoneInfo("America/New_York"))
    homer = Event("mlb.home_run", team="LAD", payload={"side": "away", "game": live, "score": "4-2", "runs": 2, "batter": "Shohei Ohtani"})
    run = Event("mlb.run", team="SD", payload={"side": "home", "game": live, "score": "4-3", "runs": 1, "batter": "Manny Machado"})
    return [
        Scene("mlb.game/pregame", MlbGameBoard(), MlbGameConfig(), with_game(pregame), now, 2.0, sizes=ALL_SIZES),
        Scene("mlb.game/live", MlbGameBoard(), MlbGameConfig(), with_game(live), now, 5.0, sizes=ALL_SIZES),
        Scene("mlb.game/break", MlbGameBoard(), MlbGameConfig(), with_game(inning_break), now, 5.0, sizes=((128, 64),)),
        Scene("mlb.game/final", MlbGameBoard(), MlbGameConfig(), with_game(final), now, 2.0, sizes=ALL_SIZES),
        Scene("mlb.ticker/slate", MlbTickerBoard(), TickerConfig(), with_game(final), now, 2.0),
        Scene("mlb.standings/division", MlbStandingsBoard(), StandingsConfig(), with_game(final), now, 3.0),
        Scene("mlb.standings/wildcard", MlbStandingsBoard(), StandingsConfig(view="wildcard"), with_game(final), now, 3.0, sizes=((128, 64),)),
        Scene("mlb.team_summary/nyy", MlbTeamSummaryBoard(), TeamSummaryConfig(), with_game(final), now, 2.0),
        Scene("mlb.score/home_run", MlbScoreBoard(), MlbScoreConfig(), with_game(live), now, 1.0, event=homer),
        Scene("mlb.score/run", MlbScoreBoard(), MlbScoreConfig(opponent_scores=True), with_game(live), now, 1.0, event=run, sizes=((128, 64),)),
    ]


# -- extras -----------------------------------------------------------------------


def extras_scenes() -> list[Scene]:
    cur, days = normalize_weather(_load("weather", "open_meteo.json"), WeatherConfig(label="Toronto"))
    wstore = SnapshotStore()
    wstore.publish("weather.current", cur)
    weather = wstore.publish("weather.daily", days)

    ac = {**normalize_aircraft(_load("flights", "adsb_lol_point.json")["ac"][0]),
          **parse_adsbdb(_load("flights", "adsbdb_callsign.json"))}
    flights = SnapshotStore().publish("flights.nearby", [ac, ac])
    overhead = Event("flights.overhead", payload={"aircraft": ac})

    holidays = SnapshotStore().publish("holidays.upcoming", [
        {"name": "Christmas Day", "display": "Christmas Day", "date": "2026-12-25", "days": 24,
         "image": str(HOLIDAY_IMAGES / "christmas_day.png"), "custom": False},
        {"name": "Game Day", "display": "Game Day", "date": "2026-12-01", "days": 0, "image": None, "custom": True},
    ])
    august = datetime(2026, 8, 26, 12, tzinfo=TORONTO)
    december = datetime(2026, 12, 1, tzinfo=TORONTO)
    return [
        Scene("weather.current/summer", WeatherBoard(), WeatherBoardConfig(), weather, august, 2.0),
        Scene("flights.nearby/two", NearbyBoard(), NearbyConfig(), flights, august, 1.0),
        Scene("flights.overhead/one", OverheadBoard(), OverheadConfig(), flights, august, 0.5, event=overhead, sizes=((128, 64),)),
        Scene("holidays.countdown/christmas", HolidayBoard(), HolidayConfig(), holidays, december, 1.0),
        Scene("holidays.countdown/custom", HolidayBoard(), HolidayConfig(seconds_per_holiday=3), holidays, december, 4.0, sizes=((128, 64),)),
    ]


# -- generic ----------------------------------------------------------------------


def generic_scenes() -> list[Scene]:
    empty = Snapshot()
    sched = _load("nhl", "schedule_now.json")
    offseason = SnapshotStore().publish("nhl.season", {**season_info(sched, datetime(2026, 8, 27).date(), 20252026), "favorite": "TOR"})
    winter = datetime(2026, 1, 15, 19, 5, tzinfo=UTC)
    summer = datetime(2026, 8, 27, 12, tzinfo=TORONTO)
    return [
        Scene("clock/evening", ClockBoard(), ClockConfig(), empty, winter, 0.0, sizes=ALL_SIZES),
        Scene("splash/intro", SplashBoard(), SplashConfig(), empty, winter, 1.0),
        Scene("splash/settled", SplashBoard(), SplashConfig(), empty, winter, 6.0),
        Scene("blank/blank", BlankBoard(), EmptyConfig(), empty, winter, 0.0, sizes=((128, 64),)),
        Scene("test_pattern/bars", TestPatternBoard(), EmptyConfig(), empty, winter, 0.0),
        Scene("season.countdown/offseason", SeasonCountdownBoard(), SeasonCountdownConfig(), offseason, summer, 2.0),
    ]


def all_scenes() -> list[Scene]:
    return [*generic_scenes(), *nhl_scenes(), *nfl_scenes(), *ncaaf_scenes(), *mlb_scenes(), *extras_scenes()]
