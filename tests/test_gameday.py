"""The game-day rollover hour: last night's finals stay in the ticker until it, today's games are never held back."""
import asyncio
import copy
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from scoreboard.config.models import AppConfig, SportsConfig
from scoreboard.data import SnapshotStore, gameday
from scoreboard.data.source import SourceContext
from scoreboard.mlb.normalize import normalize_schedule
from scoreboard.mlb.source import _slate
from scoreboard.nhl.api import BASE_URL
from scoreboard.nhl.source import NhlConfig, NhlSource

FIX = Path(__file__).parent / "fixtures"


def load(sport, name):
    return json.loads((FIX / sport / name).read_text())


class _Ctx:
    def __init__(self, hour, tz="America/Toronto"):
        self.timezone = tz
        self.game_day_rollover_hour = hour


def test_config_has_the_shared_rollover_hour():
    assert SportsConfig().game_day_rollover_hour == 10                     # last night's results until mid-morning
    assert AppConfig.model_validate({"sports": {"game_day_rollover_hour": 0}}).sports.game_day_rollover_hour == 0   # off: gone at midnight
    with pytest.raises(ValueError):
        SportsConfig(game_day_rollover_hour=13)                             # it is a morning grace window, not a day shift


def test_carry_last_night_is_before_the_hour_only(monkeypatch):
    at = {"now": datetime(2026, 4, 12, 1, 30, tzinfo=ZoneInfo("America/Toronto"))}
    monkeypatch.setattr(gameday, "local_now", lambda ctx: at["now"])
    assert gameday.carry_last_night(_Ctx(4)) is True
    assert gameday.carry_last_night(_Ctx(0)) is False                       # setting off
    assert gameday.carry_last_night(_Ctx(1)) is False                       # 01:30 is past a 1 a.m. rollover
    at["now"] = at["now"].replace(hour=4)
    assert gameday.carry_last_night(_Ctx(4)) is False                       # the hour itself is the rollover
    at["now"] = at["now"].replace(hour=23)
    assert gameday.carry_last_night(_Ctx(4)) is False                       # the evening before is not "last night"


def test_local_now_follows_the_configured_timezone():
    lhr, lax = gameday.local_now(_Ctx(0, "Europe/London")), gameday.local_now(_Ctx(0, "America/Los_Angeles"))
    assert lhr.utcoffset() != lax.utcoffset() and abs((lhr - lax).total_seconds()) < 5
    assert gameday.local_now(_Ctx(0, "Not/AZone")).tzinfo is not None      # a bad name falls back to the machine clock


def test_is_last_nights_keeps_results_and_games_still_going():
    today = "2026-04-12"
    final = {"date": "2026-04-11", "phase": "postgame", "outcome": "FINAL/OT"}
    assert gameday.is_last_nights(final, today)
    assert gameday.is_last_nights({**final, "phase": "live", "outcome": ""}, today)       # past midnight, still on
    assert not gameday.is_last_nights({**final, "outcome": "PPD"}, today)                 # not a result
    assert not gameday.is_last_nights({**final, "phase": "pregame", "outcome": ""}, today)
    assert not gameday.is_last_nights({**final, "date": "2026-04-10"}, today)             # only the night before
    assert not gameday.is_last_nights({**final, "date": today}, today)


# -- NHL: /score/now covers one league day; the other one is fetched by date ------------------

def _game(base, gid, date, state, away, home, **extra):
    g = copy.deepcopy(base)
    g.update({"id": gid, "gameDate": date, "gameState": state, "clock": {}, "periodDescriptor": {}, **extra})
    g["awayTeam"] = {**g["awayTeam"], "abbrev": away, "score": 0}
    g["homeTeam"] = {**g["homeTeam"], "abbrev": home, "score": 0}
    return g


async def _run_nhl(monkeypatch, today, mock_setup, favorites=("TOR",)):
    import scoreboard.nhl.source as src
    monkeypatch.setattr(src, "_local_today", lambda ctx: today)
    monkeypatch.setattr(src, "carry_last_night", lambda ctx: True)
    store = SnapshotStore()
    cfg = NhlConfig(favorites=list(favorites), idle_interval=15, standings_interval=300)
    async with httpx.AsyncClient() as http, respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        mock.get("/standings/now").mock(return_value=httpx.Response(200, json=load("nhl", "standings_2026-04-10.json")))
        mock.get("/club-schedule-season/TOR/now").mock(return_value=httpx.Response(200, json=load("nhl", "club_schedule_TOR_week.json")))
        mock.get("/schedule/now").mock(return_value=httpx.Response(200, json=load("nhl", "schedule_now.json")))
        mock_setup(mock)
        ctx = SourceContext("nhl", store, lambda: cfg, http)
        task = asyncio.create_task(NhlSource().run(ctx))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if store.get().has("nhl.scores", "nhl.main_event"):
                break
        task.cancel()
        return store.get()


@pytest.mark.asyncio
async def test_nhl_league_still_on_last_night_adds_todays_games(monkeypatch):
    """00:30 after the fixture's Saturday slate: /score/now still says Saturday. The ticker keeps the
    fifteen finals and gains Sunday's games; the favourite's final does not stay the main event."""
    last_night = load("nhl", "score_2026-04-11.json")
    base = last_night["games"][0]
    sunday = {"currentDate": "2026-04-12", "games": [_game(base, 1, "2026-04-12", "FUT", "MTL", "TOR"),
                                                     _game(base, 2, "2026-04-13", "FUT", "BOS", "NYR")]}    # a stray next-day game is ignored

    def routes(mock):
        mock.get("/score/now").mock(return_value=httpx.Response(200, json=last_night))
        mock.get("/score/2026-04-12").mock(return_value=httpx.Response(200, json=sunday))

    snap = await _run_nhl(monkeypatch, "2026-04-12", routes)
    scores = snap.get("nhl.scores")
    assert [g["date"] for g in scores] == ["2026-04-11"] * 15 + ["2026-04-12"]
    assert all(g["phase"] == "postgame" for g in scores[:15]) and scores[-1]["phase"] == "pregame"
    main = snap.get("nhl.main_event")
    assert main["id"] == 1 and main["phase"] == "pregame"                     # today's game, not last night's final


@pytest.mark.asyncio
async def test_nhl_league_day_turned_fetches_last_nights_results(monkeypatch):
    """/score/now has moved on to today: yesterday's finals are fetched by date and lead the ticker.
    A postponed game from yesterday is not a result and stays out."""
    today = load("nhl", "score_2026-04-11.json")
    base = today["games"][0]
    friday = {"currentDate": "2026-04-10", "games": [
        _game(base, 10, "2026-04-10", "OFF", "OTT", "MTL"),
        _game(base, 11, "2026-04-10", "FUT", "BUF", "NJD", gameScheduleState="PPD"),
    ]}

    def routes(mock):
        mock.get("/score/now").mock(return_value=httpx.Response(200, json=today))
        mock.get("/score/2026-04-10").mock(return_value=httpx.Response(200, json=friday))

    snap = await _run_nhl(monkeypatch, "2026-04-11", routes)
    scores = snap.get("nhl.scores")
    assert len(scores) == 16 and scores[0]["id"] == 10 and scores[0]["date"] == "2026-04-10"
    assert snap.get("nhl.main_event")["home"]["abbrev"] == "TOR" and snap.get("nhl.main_event")["date"] == "2026-04-11"


@pytest.mark.asyncio
async def test_nhl_last_night_fetch_failure_keeps_the_league_day(monkeypatch):
    def routes(mock):
        mock.get("/score/now").mock(return_value=httpx.Response(200, json=load("nhl", "score_2026-04-11.json")))
        mock.get("/score/2026-04-10").mock(return_value=httpx.Response(503))

    import scoreboard.nhl.api as api_mod
    monkeypatch.setattr(api_mod, "RETRY_DELAYS", (0, 0, 0))
    snap = await _run_nhl(monkeypatch, "2026-04-11", routes)
    assert len(snap.get("nhl.scores")) == 15 and snap.get("system")["online"] is True


# -- MLB: the schedule fetch already spans yesterday -----------------------------------------------

def test_mlb_slate_carries_last_nights_finals_before_the_hour():
    today = "2026-09-04"
    last_night = normalize_schedule(load("mlb", "schedule_2026-09-03.json"))         # dated 09-03: finals, live, PPD, scheduled
    finals = [g["id"] for g in last_night if g["state"] == "FINAL"]
    live = [g["id"] for g in last_night if g["state"] == "LIVE"]
    todays = [{**g, "id": f"{g['id']}t", "date": today, "state": "FUT", "phase": "pregame", "outcome": ""} for g in last_night[:2]]
    games = [*last_night, *todays]
    assert [g["id"] for g in _slate(games, today)] == [*live, *(g["id"] for g in todays)]            # off: live ones only
    carried = [g["id"] for g in _slate(games, today, carry_last_night=True)]
    assert carried == [*finals, *live, *(g["id"] for g in todays)]                  # results first; no PPD, nothing twice
    assert [g["id"] for g in _slate(last_night, today, carry_last_night=True)] == [g["id"] for g in _slate(games, today, carry_last_night=True)][:-2]
    assert _slate([g for g in last_night if g["state"] == "FINAL"], today) == []    # after the hour they are gone
