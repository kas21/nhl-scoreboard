"""Every NHL board renders at every size profile from real fixture data, and the key states look right."""
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scoreboard.boards.base import BoardContext
from scoreboard.data import Event, SnapshotStore
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
from scoreboard.render.profiles import PROFILES, profile_for

FIX = Path(__file__).parent / "fixtures" / "nhl"
SIZES = [(p.width, p.height) for p in PROFILES]


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture(scope="module")
def world():
    score = load("score_2026-04-11.json")
    standings = normalize_standings(load("standings_2026-04-10.json"))
    recs = records_from_standings(standings)
    raw = next(g for g in score["games"] if g["homeTeam"]["abbrev"] == "TOR")
    final = {**normalize_game(raw, recs, load("landing_2025021270.json")), "favorite_side": "home"}
    live = {**final, "state": "LIVE", "phase": "live", "clock": "12:34", "period": "2nd", "outcome": "",
            "powerplay": {"code": "h54", "clock": "01:12"}, "pulled_goalie": 1}
    pre = {**final, "state": "FUT", "phase": "pregame", "outcome": "", "start_time_utc": "2026-04-11T23:00:00Z"}
    store = SnapshotStore()
    store.publish("nhl.scores", [normalize_game(g, recs) for g in score["games"]])
    store.publish("nhl.standings", standings)
    store.publish("nhl.team_summary", {"TOR": team_summary("TOR", standings, load("club_schedule_TOR_week.json"), "2026-04-11")})
    return {"store": store, "final": final, "live": live, "pre": pre}


def make_ctx(world, w, h, game=None, event=None, elapsed=0.0):
    snap = world["store"].publish("main_event", game) if game else world["store"].get()
    return BoardContext(snapshot=snap, profile=profile_for(w, h), width=w, height=h, fps=30,
                        now=datetime(2026, 4, 11, 18, 30, tzinfo=ZoneInfo("America/Toronto")), elapsed=elapsed, event=event)


def has_color(img, color, tol=40):
    return any(all(abs(a - b) <= tol for a, b in zip(px, color)) for px in img.getdata())


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("phase", ["pre", "live", "final"])
def test_game_board_renders_everywhere(world, size, phase):
    img = GameBoard().render(make_ctx(world, *size, world[phase]), GameConfig())
    assert img.size == size and img.getbbox() is not None


@pytest.mark.parametrize("size", SIZES)
def test_other_boards_render_everywhere(world, size):
    for board, cfg, elapsed in [(TickerBoard(), TickerConfig(), 1.0), (StandingsBoard(), StandingsConfig(), 3.0),
                                (TeamSummaryBoard(), TeamSummaryConfig(), 0.0)]:
        img = board.render(make_ctx(world, *size, elapsed=elapsed), cfg)
        assert img.size == size and img.getbbox() is not None, type(board).__name__


def test_live_board_shows_powerplay_and_empty_net(world):
    img = GameBoard().render(make_ctx(world, 128, 64, world["live"]), GameConfig())
    assert has_color(img, (255, 200, 0))     # PP badge
    assert has_color(img, (230, 40, 40))     # EN badge


def test_goal_board_favorite_vs_opponent(world):
    live = world["live"]
    fav = Event("nhl.goal", team="TOR", payload={"side": "home", "game": live, "score": "1-3", "goal": live["goals"][-1]})
    opp = Event("nhl.goal", team="FLA", payload={"side": "away", "game": live, "score": "2-3"})
    cfg = GoalConfig()
    board = GoalBoard()
    assert board.matches(fav, cfg) and board.matches(opp, cfg)
    assert not board.matches(opp, GoalConfig(opponent_goals=False))
    ctx = make_ctx(world, 128, 64, live, fav, elapsed=2.0)
    board.enter(ctx, cfg)
    assert board._seq.duration == pytest.approx(cfg.duration, abs=0.5)
    assert board.render(ctx, cfg).getbbox() is not None
    other = GoalBoard()
    other.enter(make_ctx(world, 128, 64, live, opp), cfg)
    assert other._seq.duration < board._seq.duration       # opponent = short flash
    assert other.done(make_ctx(world, 128, 64, live, opp, elapsed=cfg.opponent_duration + 1), cfg)


def test_penalty_board_uses_description(world):
    pen = world["final"]["penalties"][0]
    ev = Event("nhl.penalty", team="TOR", payload={"penalty": pen, "game": world["live"]})
    board = PenaltyBoard()
    ctx = make_ctx(world, 128, 64, world["live"], ev, elapsed=1.0)
    img = board.render(ctx, PenaltyConfig())
    assert img.getbbox() is not None
    assert board.done(replace(ctx, elapsed=PenaltyConfig().duration + 1), PenaltyConfig())


def test_ticker_cycles_all_games_then_done(world):
    board, cfg = TickerBoard(), TickerConfig(seconds_per_game=2)
    n = len(world["store"].get().get("nhl.scores"))
    ctx = make_ctx(world, 128, 64, elapsed=0.5)
    board.render(ctx, cfg)
    assert not board.done(ctx, cfg)
    assert board.done(replace(ctx, elapsed=2 * n + 0.1), cfg)


def test_standings_scrolls_and_finishes(world):
    board, cfg = StandingsBoard(), StandingsConfig(scroll_speed=40, hold_seconds=0)
    ctx = make_ctx(world, 128, 64)
    first = board.render(ctx, cfg)
    later = board.render(replace(ctx, elapsed=2.0), cfg)
    assert first.tobytes() != later.tobytes()
    assert not board.done(replace(ctx, elapsed=0.5), cfg)
    assert board.done(replace(ctx, elapsed=60.0), cfg)
