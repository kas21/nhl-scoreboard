import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scoreboard.boards.base import BoardContext
from scoreboard.data import Event, SnapshotStore
from scoreboard.data.arbiter import MainEventArbiter, choose
from scoreboard.nfl.boards.game import NflGameBoard, NflGameConfig
from scoreboard.nfl.boards.others import (
    NflScoreBoard,
    NflStandingsBoard,
    NflTeamSummaryBoard,
    NflTickerBoard,
    ScoreConfig,
)
from scoreboard.nfl.events import detect_nfl
from scoreboard.nfl.normalize import (
    normalize_game,
    normalize_scoreboard,
    normalize_standings,
    team_summary,
)
from scoreboard.nhl.boards.standings import StandingsConfig
from scoreboard.nhl.boards.team_summary import TeamSummaryConfig
from scoreboard.nhl.boards.ticker import TickerConfig
from scoreboard.render.profiles import profile_for

FIX = Path(__file__).parent / "fixtures" / "nfl"


def load(n):
    return json.loads((FIX / n).read_text())


def test_normalize_scoreboard_and_standings():
    games = normalize_scoreboard(load("espn_scoreboard.json"))
    assert len(games) == 16 and all(g["sport"] == "nfl" for g in games)
    g = games[0]
    assert g["phase"] == "postgame" and g["outcome"] == "FINAL" and g["away"]["abbrev"] and g["home"]["abbrev"]
    assert g["away"]["record"].count("-") >= 1 and isinstance(g["away"]["color"], tuple)
    st = normalize_standings(load("espn_standings.json"))
    assert len(st["teams"]) == 32 and len(st["division"]) == 8 and all(len(v) == 4 for v in st["division"].values())
    assert st["teams"]["BUF"]["wins"] == 2 and st["teams"]["BUF"]["streak"] == "W2"
    ts = team_summary("BUF", st, load("espn_schedule_BUF.json"), "2026-08-26")
    assert ts["record"]["wins"] == 2 and ts["prev_game"]["result"] in ("W", "L", "T") and ts["next_game"]["opponent"]


def test_live_situation_and_labels():
    ev = load("espn_scoreboard.json")["events"][0]
    comp = ev["competitions"][0]
    away_id = next(c["team"]["id"] for c in comp["competitors"] if c["homeAway"] == "away")
    comp["status"] = {"period": 3, "displayClock": "7:12", "type": {"state": "in", "name": "STATUS_IN_PROGRESS"}}
    comp["situation"] = {"down": 2, "distance": 7, "possession": away_id, "isRedZone": True, "homeTimeouts": 2, "awayTimeouts": 3, "shortDownDistanceText": "2nd & 7"}
    g = normalize_game(ev)
    assert g["phase"] == "live" and g["period"] == "3rd" and g["clock"] == "7:12"
    assert g["situation"]["possession"] == "away" and g["situation"]["red_zone"] and g["home"]["timeouts"] == 2
    comp["status"]["type"]["name"] = "STATUS_HALFTIME"; comp["status"]["period"] = 2
    assert normalize_game(ev)["phase"] == "intermission" and normalize_game(ev)["period"] == "HALF"
    comp["status"] = {"period": 5, "displayClock": "0:00", "type": {"state": "post", "name": "STATUS_FINAL"}}
    assert normalize_game(ev)["outcome"] == "FINAL/OT"


def test_arbiter_prefers_live_then_priority():
    nhl = {"sport": "nhl", "phase": "pregame"}
    nfl = {"sport": "nfl", "phase": "live"}
    assert choose({"nhl": nhl, "nfl": nfl}, ["nhl", "nfl"]) is nfl
    assert choose({"nhl": nhl, "nfl": {"sport": "nfl", "phase": "pregame"}}, ["nhl", "nfl"]) is nhl
    assert choose({"nhl": None, "nfl": None}, ["nhl"]) is None
    store = SnapshotStore()
    MainEventArbiter(store, lambda: ["nhl", "nfl"])
    store.publish("nfl.main_event", nfl)
    assert store.get().get("main_event") is nfl
    store.publish("nhl.main_event", {"sport": "nhl", "phase": "live"})
    assert store.get().get("main_event")["sport"] == "nhl"


def test_scoring_events():
    store = SnapshotStore()
    g = normalize_scoreboard(load("espn_scoreboard.json"))[0]
    base = {**g, "state": "LIVE", "phase": "live", "away": {**g["away"], "score": 0}, "home": {**g["home"], "score": 0}}
    s0 = store.publish("nfl.main_event", base)
    s1 = store.publish("nfl.main_event", {**base, "away": {**base["away"], "score": 7}})
    s2 = store.publish("nfl.main_event", {**base, "away": {**base["away"], "score": 7}, "home": {**base["home"], "score": 3}})
    assert [e.kind for e in detect_nfl(s0, s1)] == ["nfl.touchdown"]
    assert [e.kind for e in detect_nfl(s1, s2)] == ["nfl.field_goal"]


def test_nfl_boards_render():
    games = normalize_scoreboard(load("espn_scoreboard.json"))
    st = normalize_standings(load("espn_standings.json"))
    live = {**games[0], "state": "LIVE", "phase": "live", "period": "3rd", "clock": "7:12", "outcome": "", "favorite_side": "home",
            "situation": {"possession": "home", "down": 2, "distance": 7, "red_zone": True, "text": "2nd & 7", "last_play": ""}}
    live["home"] = {**live["home"], "timeouts": 2}
    store = SnapshotStore()
    store.publish("nfl.scores", games); store.publish("nfl.standings", st)
    store.publish("nfl.team_summary", {"BUF": team_summary("BUF", st, load("espn_schedule_BUF.json"), "2026-08-26")})
    snap = store.publish("main_event", live)
    now = datetime(2026, 8, 26, 13, tzinfo=ZoneInfo("America/Toronto"))
    def ctx(t, ev=None, w=128, h=64):
        return BoardContext(snapshot=snap, profile=profile_for(w, h), width=w, height=h, fps=30, now=now, elapsed=t, event=ev)
    for board, cfg in [(NflGameBoard(), NflGameConfig()), (NflTickerBoard(), TickerConfig()), (NflStandingsBoard(), StandingsConfig()), (NflTeamSummaryBoard(), TeamSummaryConfig())]:
        img = board.render(ctx(2.0), cfg)
        assert img.size == (128, 64) and img.getbbox() is not None, board.key
    ev = Event("nfl.touchdown", team=live["home"]["abbrev"], payload={"side": "home", "game": live, "score": "7-0", "points": 7})
    sb = NflScoreBoard()
    assert sb.matches(ev, ScoreConfig())
    assert sb.render(ctx(1.0, ev), ScoreConfig()).getbbox() is not None
    fg = Event("nfl.field_goal", team=live["away"]["abbrev"], payload={"side": "away", "game": live, "score": "7-3", "points": 3})
    assert NflScoreBoard().render(ctx(1.0, fg), ScoreConfig()).getbbox() is not None
