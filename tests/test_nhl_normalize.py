import json
from pathlib import Path

import httpx
import pytest
import respx

from scoreboard.data import SnapshotStore
from scoreboard.nhl.api import BASE_URL, NhlApi, NhlApiError
from scoreboard.nhl.events import detect_main_event
from scoreboard.nhl.normalize import (
    normalize_game,
    normalize_standings,
    outcome_label,
    period_label,
    records_from_standings,
    situation,
    team_summary,
)
from scoreboard.nhl.select import favorite_side, select_main_event

FIX = Path(__file__).parent / "fixtures" / "nhl"


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def score():
    return load("score_2026-04-11.json")


@pytest.fixture
def standings():
    return normalize_standings(load("standings_2026-04-10.json"))


def test_situation_codes():
    assert situation("1551") == ("ev", 0)
    assert situation("1541") == ("a54", 0)
    assert situation("1451") == ("h54", 0)
    assert situation("0651") == ("a65", 1)
    assert situation("1560") == ("h65", 2)
    assert situation("0660") == ("ev", 3)
    assert situation(None) == ("ev", 0)
    assert situation("1010") == ("ev", 0)


def test_period_and_outcome_labels():
    assert period_label({"number": 2, "periodType": "REG"}, 2) == "2nd"
    assert period_label({"number": 4, "periodType": "OT", "maxRegulationPeriods": 3}, 2) == "OT"
    assert period_label({"number": 5, "periodType": "OT", "maxRegulationPeriods": 3}, 3) == "2OT"
    assert period_label({"number": 5, "periodType": "SO"}, 2) == "SO"
    assert outcome_label({"gameState": "LIVE"}) == ""
    assert outcome_label({"gameState": "OFF", "gameOutcome": {"lastPeriodType": "SO"}}) == "FINAL/SO"
    assert outcome_label({"gameState": "FINAL", "gameOutcome": {"lastPeriodType": "OT", "otPeriods": 2}}) == "FINAL/2OT"


def test_normalize_game_from_score_and_landing(score, standings):
    raw = next(g for g in score["games"] if g["homeTeam"]["abbrev"] == "TOR")
    g = normalize_game(raw, records_from_standings(standings), load("landing_2025021270.json"))
    assert g["id"] == 2025021270 and g["phase"] == "postgame" and g["outcome"] == "FINAL"
    assert g["away"] == {"abbrev": "FLA", "name": "Panthers", "city": "Florida", "score": 6, "sog": 25, "record": g["away"]["record"]}
    assert g["away"]["record"].count("-") == 2
    assert g["home"]["abbrev"] == "TOR" and g["home"]["city"] == "Toronto"
    assert g["period"] == "3rd" and g["clock"] == "00:00"
    assert g["goals"][0]["scorer"] == "E. Luostarinen" and g["goals"][0]["assists"] == ["M. Samoskevich"]
    assert g["penalties"][0]["team"] == "TOR" and g["penalties"][0]["duration"] == 2
    assert g["powerplay"] == {"code": "ev", "clock": ""} and g["pulled_goalie"] == 0


def test_normalize_game_live_intermission_fallback():
    raw = {"id": 1, "gameState": "LIVE", "awayTeam": {"abbrev": "A"}, "homeTeam": {"abbrev": "B"},
           "clock": {"timeRemaining": "00:00", "running": False, "inIntermission": False},
           "periodDescriptor": {"number": 1, "periodType": "REG"}, "situation": {"situationCode": "1451", "timeRemaining": "01:20"}}
    g = normalize_game(raw)
    assert g["phase"] == "intermission" and g["in_intermission"]
    assert g["powerplay"] == {"code": "h54", "clock": "01:20"}


def test_normalize_standings_shapes(standings):
    assert len(standings["teams"]) == 32
    assert set(standings["division"]) == {"Atlantic", "Metropolitan", "Central", "Pacific"}
    assert all(len(v) == 8 for v in standings["division"].values())
    east = standings["wildcard"]["Eastern"]
    assert set(east) == {"Atlantic", "Metropolitan", "Wildcard"}
    assert len(east["Atlantic"]) == 3 and len(east["Wildcard"]) == 10
    assert standings["league"][0] == "COL"
    assert standings["teams"]["COL"]["streak"] == "W2" and standings["teams"]["COL"]["l10"] == [7, 3, 0]


def test_team_summary(standings):
    ts = team_summary("TOR", standings, load("club_schedule_TOR_week.json"), today="2026-04-11")
    assert ts["record"]["points"] == standings["teams"]["TOR"]["points"]
    finished = [g for g in load("club_schedule_TOR_week.json")["games"] if g["gameState"] == "OFF"]
    assert ts["prev_game"]["id"] == finished[-1]["id"] and ts["prev_game"]["result"] in ("W", "L")
    assert ts["next_game"] is None or ts["next_game"]["date"] >= "2026-04-11"


def test_select_main_event(score):
    games = [normalize_game(g) for g in score["games"]]
    assert select_main_event(games, ["TOR"])["home"]["abbrev"] == "TOR"
    assert select_main_event(games, ["XXX"]) is None
    live = dict(games[0]); live = {**live, "state": "LIVE", "away": {**live["away"], "abbrev": "ZZZ"}}
    chosen = select_main_event([*games, live], ["TOR", "ZZZ"])
    assert chosen["state"] == "LIVE"                      # live beats favourite order
    assert favorite_side(games[0], [games[0]["home"]["abbrev"]]) == "home"


def test_detect_goal_penalty_state_events():
    store = SnapshotStore()
    base = normalize_game({"id": 7, "gameState": "LIVE", "awayTeam": {"abbrev": "TOR", "score": 0}, "homeTeam": {"abbrev": "MTL", "score": 0},
                           "periodDescriptor": {"number": 1, "periodType": "REG"}})
    s0 = store.publish("nhl.main_event", base)
    scored = {**base, "away": {**base["away"], "score": 1}, "state": "CRIT",
              "goals": [{"team": "TOR", "scorer": "A. Matthews", "assists": [], "period": 1, "time": "05:00"}],
              "penalties": [{"team": "MTL", "type": "MIN", "duration": 2, "desc": "tripping", "player": "X", "period": 1, "time": "04:00"}],
              "powerplay": {"code": "a54", "clock": "01:59"}}
    s1 = store.publish("nhl.main_event", scored)
    kinds = {e.kind: e for e in detect_main_event(s0, s1)}
    assert set(kinds) == {"nhl.goal", "nhl.penalty", "nhl.state_change", "nhl.powerplay"}
    assert kinds["nhl.goal"].team == "TOR" and kinds["nhl.goal"].payload["goal"]["scorer"] == "A. Matthews"
    assert kinds["nhl.penalty"].payload["penalty"]["desc"] == "tripping"
    other = store.publish("nhl.main_event", {**scored, "id": 8})
    assert list(detect_main_event(s1, other)) == []      # different game: no stale events


@pytest.mark.asyncio
async def test_api_retries_then_succeeds_and_handles_429():
    async with httpx.AsyncClient() as http, respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/score/now")
        route.side_effect = [httpx.ConnectError("boom"), httpx.Response(429, headers={"Retry-After": "0"}),
                             httpx.Response(307, headers={"Location": f"{BASE_URL}/score/2026-09-29"})]
        mock.get("/score/2026-09-29").mock(return_value=httpx.Response(200, json={"games": []}))
        import scoreboard.nhl.api as api_mod
        api_mod.RETRY_DELAYS = (0, 0, 0)
        assert await NhlApi(http).score() == {"games": []}
        mock.get("/standings/now").mock(return_value=httpx.Response(500))
        with pytest.raises(NhlApiError):
            await NhlApi(http).standings()


def test_select_main_event_ignores_future_game_days(score):
    games = [normalize_game(g) for g in score["games"]]          # all dated 2026-04-11
    assert select_main_event(games, ["TOR"], today="2026-04-11") is not None
    assert select_main_event(games, ["TOR"], today="2026-04-10") is None
    live = {**games[0], "state": "LIVE", "away": {**games[0]["away"], "abbrev": "TOR"}}
    assert select_main_event([live], ["TOR"], today="2026-04-10") is live   # active games always count


# -- schedule state (postponed / suspended / cancelled) ---------------------------

def _tor_raw(score):
    return next(g for g in score["games"] if g["homeTeam"]["abbrev"] == "TOR")


@pytest.mark.parametrize("sched, state, outcome", [
    ("PPD", "PPD", "PPD"), ("SUSP", "SUSP", "SUSPENDED"), ("CNCL", "CNCL", "CANCELLED"),
])
def test_schedule_state_overrides_game_state(score, sched, state, outcome):
    raw = {**_tor_raw(score), "gameState": "FUT", "gameScheduleState": sched}
    g = normalize_game(raw)
    assert (g["state"], g["phase"], g["outcome"]) == (state, "postgame", outcome)
    assert g["schedule_state"] == sched


def test_schedule_state_ok_or_absent_changes_nothing(score):
    raw = _tor_raw(score)
    with_ok = normalize_game({**raw, "gameScheduleState": "OK"})
    without = normalize_game({k: v for k, v in raw.items() if k != "gameScheduleState"})
    assert with_ok == without
    assert with_ok["schedule_state"] == "OK" and with_ok["outcome"].startswith("FINAL")


def test_postponed_game_is_never_active_and_ranks_below_played_games(score):
    from scoreboard.nhl.normalize import ACTIVE_STATES

    ppd = normalize_game({**_tor_raw(score), "gameState": "FUT", "gameScheduleState": "PPD"})
    assert ppd["state"] not in ACTIVE_STATES
    played = normalize_game(next(g for g in score["games"] if g["homeTeam"]["abbrev"] != "TOR"))
    ppd_fla = {**ppd, "home": {**ppd["home"], "abbrev": played["away"]["abbrev"]}}
    assert select_main_event([ppd_fla, played], [played["away"]["abbrev"]], today=played["date"]) is played
    assert select_main_event([ppd], ["TOR"], today=ppd["date"]) is ppd          # still shown when it is the only one


def test_shootout_is_live_not_intermission():
    """A shootout has no clock: 00:00, not running, inIntermission false. That used to trip the
    stopped-clock-means-intermission fallback, so the board said INT instead of SO."""
    raw = {"id": 1, "gameState": "LIVE", "awayTeam": {"abbrev": "A"}, "homeTeam": {"abbrev": "B"},
           "clock": {"timeRemaining": "00:00", "secondsRemaining": 0, "running": False, "inIntermission": False},
           "periodDescriptor": {"number": 5, "periodType": "SO"}}
    g = normalize_game(raw)
    assert g["phase"] == "live" and not g["in_intermission"] and g["period"] == "SO"


def test_halftime_game_stays_the_main_event(score):
    from scoreboard.nhl.select import select_main_event
    live = {"id": 1, "state": "HALF", "date": "2026-04-11", "away": {"abbrev": "KC"}, "home": {"abbrev": "BUF"}}
    pre = {"id": 2, "state": "PRE", "date": "2026-04-11", "away": {"abbrev": "DAL"}, "home": {"abbrev": "NYG"}}
    assert select_main_event([pre, live], ["KC", "DAL"], today="2026-04-11") is live
    # ...and past local midnight a halftime game is still the game being played.
    assert select_main_event([{**live, "date": "2026-04-10"}], ["KC"], today="2026-04-11")["id"] == 1
    assert select_main_event([{**live, "state": "POST", "date": "2026-04-10"}], ["KC"], today="2026-04-11") is None


def test_penalties_are_detected_by_identity_not_by_count():
    from scoreboard.data import SnapshotStore
    from scoreboard.nhl.events import detect_main_event
    from scoreboard.nhl.source import _carry_landing
    store = SnapshotStore()

    def pen(n, t="04:00"):
        return {"team": "MTL", "type": "MIN", "duration": 2, "desc": f"p{n}", "player": "X", "period": 1, "time": t}

    base = {"id": 7, "state": "LIVE", "away": {"abbrev": "MTL", "score": 0}, "home": {"abbrev": "TOR", "score": 0},
            "goals": [], "powerplay": {"code": "h54", "clock": "01:00"}, "pulled_goalie": 0}
    s0 = store.publish("nhl.main_event", {**base, "penalties": [pen(1), pen(2, "09:00")]})
    # The landing feed failed for one poll: the score feed alone has no penalties. Carried, so
    # nothing is announced now...
    carried = _carry_landing({**base, "penalties": [], "powerplay": {"code": "ev", "clock": ""}}, s0.get("nhl.main_event"))
    assert carried["penalties"] == [pen(1), pen(2, "09:00")] and carried["powerplay"]["code"] == "h54"
    s1 = store.publish("nhl.main_event", carried)
    assert [e.kind for e in detect_main_event(s0, s1)] == []
    # ...and when it comes back with a third penalty, only that one is new, even reordered.
    s2 = store.publish("nhl.main_event", {**base, "penalties": [pen(3, "12:00"), pen(1), pen(2, "09:00")]})
    events = [e for e in detect_main_event(s1, s2) if e.kind == "nhl.penalty"]
    assert [e.payload["penalty"]["desc"] for e in events] == ["p3"]
    assert _carry_landing({**base, "id": 8, "penalties": []}, s2.get("nhl.main_event"))["penalties"] == []     # another game: nothing carried


def test_an_evening_game_is_todays_game_east_of_the_atlantic_too():
    """The NHL dates a game by the Eastern day. A 7 pm ET puck drop is 01:00 the next day in
    Berlin, where the local date already differs from the league date; the game must still
    count as today's, or a European never sees a pregame or postgame board."""
    from scoreboard.nhl.select import on_day, select_main_event
    g = {"id": 1, "state": "FUT", "date": "2026-04-11", "start_time_utc": "2026-04-11T23:00:00Z",
         "away": {"abbrev": "TOR"}, "home": {"abbrev": "BOS"}}
    assert on_day(g, "2026-04-12", "Europe/Berlin") and not on_day(g, "2026-04-12", "America/Toronto")
    assert select_main_event([g], ["TOR"], today="2026-04-12", timezone="Europe/Berlin") is g
    assert select_main_event([g], ["TOR"], today="2026-04-12", timezone="America/Toronto") is None
    assert select_main_event([{**g, "state": "OFF"}], ["TOR"], today="2026-04-12", timezone="Europe/Berlin")["state"] == "OFF"    # and the postgame board
    assert select_main_event([g], ["TOR"], today="2026-04-13", timezone="Europe/Berlin") is None                                  # but not the day after
    assert on_day({**g, "start_time_utc": "garbage"}, "2026-04-12", "Europe/Berlin") is False
