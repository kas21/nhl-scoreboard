import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from PIL import ImageChops

from scoreboard.boards.base import BoardContext
from scoreboard.data import Event, SnapshotStore
from scoreboard.data.arbiter import MainEventArbiter, choose
from scoreboard.data.source import SourceContext
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
    schedule_games,
    team_summary,
)
from scoreboard.nfl.source import NflConfig, NflSource, poll_active
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


def test_situation_carries_the_spot_and_a_tidy_last_play():
    ev = load("espn_scoreboard.json")["events"][0]
    comp = ev["competitions"][0]
    comp["status"] = {"period": 3, "displayClock": "7:12", "type": {"state": "in", "name": "STATUS_IN_PROGRESS"}}
    comp["situation"] = {"possessionText": "KC 44", "shortDownDistanceText": "3rd & 7",
                         "lastPlay": {"text": " (Shotgun) P.Mahomes pass complete to T.Kelce for 12 yards. "}}
    sit = normalize_game(ev)["situation"]
    assert sit["spot"] == "KC 44" and sit["text"] == "3rd & 7"
    assert sit["last_play"] == "(Shotgun) P.Mahomes pass complete to T.Kelce for 12 yards."
    comp["situation"] = {"lastPlay": {"text": "x" * 300}}
    sit = normalize_game(ev)["situation"]
    assert sit["spot"] == "" and len(sit["last_play"]) == 80
    comp["situation"] = {}
    assert normalize_game(ev)["situation"]["last_play"] == ""


def _live_nfl_snapshot(**situation):
    games = normalize_scoreboard(load("espn_scoreboard.json"))
    sit = {"possession": "home", "down": 3, "distance": 7, "red_zone": False, "text": "3rd & 7", "spot": "", "last_play": "", **situation}
    live = {**games[0], "state": "LIVE", "phase": "live", "period": "3rd", "clock": "7:12", "outcome": "", "favorite_side": "home", "situation": sit}
    return SnapshotStore().publish("main_event", live)


def _nfl_frame(snap, cfg: NflGameConfig, t: float = 2.0):
    now = datetime(2026, 8, 26, 13, tzinfo=ZoneInfo("America/Toronto"))
    ctx = BoardContext(snapshot=snap, profile=profile_for(128, 64), width=128, height=64, fps=30, now=now, elapsed=t)
    return NflGameBoard().render(ctx, cfg)


def test_nfl_game_board_shows_the_spot_under_down_and_distance():
    with_spot = _nfl_frame(_live_nfl_snapshot(spot="KC 44"), NflGameConfig())
    without = _nfl_frame(_live_nfl_snapshot(), NflGameConfig())
    hidden = _nfl_frame(_live_nfl_snapshot(spot="KC 44"), NflGameConfig(show_field_position=False))
    box = ImageChops.difference(with_spot, without).getbbox()
    assert box is not None and box[0] >= 34 and box[2] <= 94 and box[1] >= 50 and box[3] <= 56, box
    assert ImageChops.difference(hidden, without).getbbox() is None


def test_nfl_game_board_scrolls_the_last_play_along_the_bottom():
    play = "P.Mahomes pass complete to T.Kelce for 12 yards to the DEN 33"
    snap = _live_nfl_snapshot(last_play=play)
    with_play = _nfl_frame(snap, NflGameConfig())
    without = _nfl_frame(_live_nfl_snapshot(), NflGameConfig())
    hidden = _nfl_frame(snap, NflGameConfig(show_last_play=False))
    box = ImageChops.difference(with_play, without).getbbox()
    assert box is not None and box[0] >= 10 and box[2] <= 118 and box[1] >= 55 and box[3] <= 61, box
    assert ImageChops.difference(hidden, without).getbbox() is None
    strip = (12, 56, 116, 61)                                               # below the logos, so no sheen in the crop
    later = _nfl_frame(snap, NflGameConfig(), t=4.0)
    assert ImageChops.difference(with_play.crop(strip), later.crop(strip)).getbbox() is not None   # too wide: it marquees


def test_game_date_is_the_local_calendar_day_when_a_timezone_is_given():
    ev = load("espn_scoreboard.json")["events"][0]
    ev["date"] = "2026-09-15T00:15Z"                                        # Monday night, 8:15 pm Eastern
    assert normalize_game(ev)["date"] == "2026-09-15"                       # UTC by default (fixtures, goldens)
    assert normalize_game(ev, tz=ZoneInfo("America/New_York"))["date"] == "2026-09-14"
    assert schedule_games({"events": [ev]}, tz=ZoneInfo("America/New_York"))[0]["date"] == "2026-09-14"
    summary = team_summary("HOU", None, {"events": [ev]}, "2026-09-14", tz=ZoneInfo("America/New_York"))
    assert summary["prev_game"]["date"] == "2026-09-14"                   # the fixture game is final


def test_poll_cadence_is_live_for_a_game_in_progress_or_a_pregame_today():
    assert poll_active({"phase": "live", "date": "2026-09-15"}, "2026-09-14")          # in progress: the date can't demote it
    assert poll_active({"phase": "intermission", "date": "2026-09-15"}, "2026-09-14")
    assert poll_active({"phase": "pregame", "date": "2026-09-14"}, "2026-09-14")
    assert not poll_active({"phase": "pregame", "date": "2026-09-15"}, "2026-09-14")
    assert not poll_active({"phase": "postgame", "date": "2026-09-14"}, "2026-09-14")
    assert not poll_active(None, "2026-09-14")


class _StopLoop(Exception):
    pass


@pytest.mark.asyncio
async def test_scores_loop_polls_a_night_game_at_the_live_interval(monkeypatch):
    """A 00:15Z kickoff is the evening of the local day: the loop must sleep the live interval, not the idle one."""
    from scoreboard.nfl import source as nfl_source
    payload = load("espn_scoreboard.json")
    ev = payload["events"][0]
    ev["date"] = ev["competitions"][0]["date"] = "2026-09-15T00:15Z"
    ev["competitions"][0]["status"] = {"period": 0, "displayClock": "0:00", "type": {"state": "pre", "name": "STATUS_SCHEDULED"}}
    monkeypatch.setattr(nfl_source, "_today", lambda ctx: "2026-09-14")
    slept: list[float] = []

    async def stop_after_recording(seconds: float, **_: object) -> None:
        slept.append(seconds)
        raise _StopLoop

    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/nfl/scoreboard.*").mock(return_value=httpx.Response(200, json=payload))
        cfg = NflConfig(favorites=["HOU"], live_interval=20, idle_interval=300)
        store = SnapshotStore()
        ctx = SourceContext(key="nfl", store=store, config_getter=lambda: cfg, http=http)
        ctx.timezone = "America/New_York"
        ctx.sleep = stop_after_recording
        src = NflSource()
        with pytest.raises(_StopLoop):
            await src._scores_loop(ctx, src._api(ctx))
        main = store.get().get("nfl.main_event")
    assert main["id"] == ev["id"] and main["date"] == "2026-09-14"
    assert slept == [20]


@pytest.mark.asyncio
async def test_espn_requests_use_a_user_agent_espn_accepts():
    """site.api.espn.com 403s custom user agents; the app-wide one must not leak into these calls."""
    from scoreboard import espn
    from scoreboard.nfl.api import NflApi

    seen = {}

    def capture(request):
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json={"events": []})

    async with httpx.AsyncClient(headers={"User-Agent": "nhl-scoreboard"}) as http, respx.mock() as mock:
        mock.get(url__regex=r"https://site\.api\.espn\.com/.*").mock(side_effect=capture)
        await NflApi(http).scoreboard()
    assert seen["ua"] == espn.API_UA and "nhl-scoreboard" not in seen["ua"]
