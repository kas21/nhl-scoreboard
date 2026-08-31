"""What the NHL feed has to keep providing for the panel to be *right*.

`api-web.nhle.com` is undocumented and unversioned, and `normalize.py` is deliberately
forgiving — `_text()`, `or {}`, `int(x or 0)`. That is the correct shape for one flaky
response and the wrong shape for a permanent rename: if `situationCode` becomes something
else, nothing raises. `situation()` returns "ev", `outcome_label()` returns "", the board
renders, and the sign shows a confident, plausible, wrong scoreboard. Silent wrongness is
worse here than a crash, because a crash is quarantined and visibly falls back to the clock.

So the fields normalize actually reads are written down, and checked two ways:

  * against the recorded fixtures on every run — this proves the spec below describes the
    real payloads rather than what someone assumed they contained;
  * against the live API when asked, which is the part that catches drift:

        SCOREBOARD_CONTRACT_TEST=1 uv run pytest tests/test_nhl_contract.py -q

Run the live pass on a schedule, not in the fast suite. It needs the network, and during
the off-season the score feed legitimately carries no games — that is reported as a skip,
not a pass, so an empty slate can never be mistaken for a green contract.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

FIX = Path(__file__).parent / "fixtures" / "nhl"
BASE = "https://api-web.nhle.com/v1"
LIVE = os.environ.get("SCOREBOARD_CONTRACT_TEST") == "1"
live_only = pytest.mark.skipif(not LIVE, reason="set SCOREBOARD_CONTRACT_TEST=1 to check the live API")


# -- the spec ------------------------------------------------------------------
# Every name here is read by scoreboard/nhl/normalize.py. Grouped by the object it must
# appear on, so a failure names the payload shape that moved rather than just a key.

GAME_FIELDS = ("id", "gameType", "gameState", "gameDate", "startTimeUTC", "awayTeam", "homeTeam")
# periodDescriptor and clock are absent from games that have not started — confirmed against
# the live feed in the off-season, where every game is FUT and carries neither. normalize_game
# already reads both through `or {}`, so this is the real contract, not a workaround.
STARTED_STATES = ("LIVE", "CRIT", "OVER", "FINAL", "OFF")
TEAM_FIELDS = ("abbrev",)
TEAM_STARTED_FIELDS = ("abbrev", "score")     # no score on a game that has not been played yet
PERIOD_FIELDS = ("number", "periodType")
CLOCK_FIELDS = ("timeRemaining", "running", "inIntermission")
STANDINGS_FIELDS = ("teamAbbrev", "conferenceName", "divisionName", "gamesPlayed", "wins", "losses",
                    "otLosses", "points", "l10Wins", "l10Losses", "l10OtLosses", "streakCode",
                    "streakCount", "divisionSequence", "conferenceSequence", "leagueSequence",
                    "wildcardSequence")
GOAL_FIELDS = ("teamAbbrev", "timeInPeriod", "name", "firstName", "lastName", "goalsToDate",
               "strength", "assists", "awayScore", "homeScore")
PENALTY_FIELDS = ("teamAbbrev", "timeInPeriod", "type", "duration", "descKey")

# Values normalize maps through a lookup rather than passing along: an unknown one is not a
# missing field, it is a state the director cannot classify, so it must not appear quietly.
KNOWN_GAME_STATES = {"FUT", "PRE", "LIVE", "CRIT", "OVER", "FINAL", "OFF"}
KNOWN_PERIOD_TYPES = {"REG", "OT", "SO"}


def missing(obj: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [f for f in fields if f not in obj]


def check_score_payload(payload: dict[str, Any], where: str) -> int:
    """Assert a /score response still carries what normalize_game reads. Returns game count."""
    assert "games" in payload, f"{where}: no 'games' key"
    games = payload["games"] or []
    for game in games:
        tag = f"{where} game {game.get('id')}"
        assert not missing(game, GAME_FIELDS), f"{tag}: missing {missing(game, GAME_FIELDS)}"
        assert game["gameState"] in KNOWN_GAME_STATES, f"{tag}: unknown gameState {game['gameState']!r}"
        started = game["gameState"] in STARTED_STATES
        wanted = TEAM_STARTED_FIELDS if started else TEAM_FIELDS
        for side in ("awayTeam", "homeTeam"):
            team = game[side]
            assert not missing(team, wanted), f"{tag} {side}: missing {missing(team, wanted)}"
        descriptor = game.get("periodDescriptor") or {}
        assert descriptor or not started, f"{tag}: started but has no periodDescriptor — the period label goes blank"
        if descriptor:
            assert not missing(descriptor, PERIOD_FIELDS), f"{tag}: periodDescriptor {missing(descriptor, PERIOD_FIELDS)}"
            assert descriptor["periodType"] in KNOWN_PERIOD_TYPES, f"{tag}: unknown periodType {descriptor['periodType']!r}"
        if game["gameState"] in ("LIVE", "CRIT"):
            clock = game.get("clock") or {}
            assert not missing(clock, CLOCK_FIELDS), f"{tag}: clock {missing(clock, CLOCK_FIELDS)}"
    return len(games)


def check_landing_payload(landing: dict[str, Any], where: str) -> None:
    """The landing feed is where the power play, goals and penalties come from."""
    assert not missing(landing, ("id", "gameState", "awayTeam", "homeTeam")), f"{where}: {missing(landing, ('id', 'gameState', 'awayTeam', 'homeTeam'))}"
    situation = landing.get("situation")
    if situation:                            # only present while a game is actually being played
        assert "situationCode" in situation, f"{where}: situation lost situationCode — power play and pulled goalie go silently wrong"
        code = situation["situationCode"]
        assert isinstance(code, str) and len(code) == 4 and code.isdigit(), f"{where}: situationCode {code!r} is no longer 4 digits"
    summary = landing.get("summary") or {}
    for period in summary.get("scoring") or []:
        for goal in period.get("goals") or []:
            assert not missing(goal, GOAL_FIELDS), f"{where}: goal missing {missing(goal, GOAL_FIELDS)}"
    for period in summary.get("penalties") or []:
        for pen in period.get("penalties") or []:
            assert not missing(pen, PENALTY_FIELDS), f"{where}: penalty missing {missing(pen, PENALTY_FIELDS)}"


def check_standings_payload(payload: dict[str, Any], where: str) -> None:
    assert payload.get("standings"), f"{where}: no standings rows"
    for row in payload["standings"]:
        assert not missing(row, STANDINGS_FIELDS), f"{where}: {row.get('teamAbbrev')} missing {missing(row, STANDINGS_FIELDS)}"


# -- the spec describes the captures (runs offline, every time) -----------------

def test_spec_matches_the_recorded_score_feed():
    assert check_score_payload(json.loads((FIX / "score_2026-04-11.json").read_text()), "fixture score") > 0


def test_spec_matches_the_recorded_landing_feed():
    check_landing_payload(json.loads((FIX / "landing_2025021270.json").read_text()), "fixture landing")


def test_spec_matches_the_recorded_standings():
    check_standings_payload(json.loads((FIX / "standings_2026-04-10.json").read_text()), "fixture standings")


# -- and the live API still matches the spec (opt-in) ---------------------------

@pytest.fixture(scope="module")
def api() -> httpx.Client:
    with httpx.Client(base_url=BASE, timeout=20, follow_redirects=True,
                      headers={"User-Agent": "nhl-scoreboard-contract-test"}) as client:
        yield client


@live_only
def test_live_score_feed_still_has_what_the_boards_read(api):
    payload = api.get("/score/now").raise_for_status().json()
    if check_score_payload(payload, "live /score/now") == 0:
        pytest.skip("no games on the slate today — an empty slate proves nothing either way")


@live_only
def test_live_landing_feed_still_has_what_the_boards_read(api):
    games = api.get("/score/now").raise_for_status().json().get("games") or []
    played = [g for g in games if g.get("gameState") in ("LIVE", "CRIT", "OVER", "FINAL", "OFF")]
    if not played:
        pytest.skip("no game has started today; the landing feed has nothing to check yet")
    check_landing_payload(api.get(f"/gamecenter/{played[0]['id']}/landing").raise_for_status().json(),
                          f"live landing {played[0]['id']}")


@live_only
def test_live_standings_still_has_what_the_boards_read(api):
    check_standings_payload(api.get("/standings/now").raise_for_status().json(), "live /standings/now")


@live_only
def test_the_live_feed_still_normalises_end_to_end(api):
    """The spec checks names; this checks the result is usable — a game the boards can draw."""
    from scoreboard.nhl.normalize import normalize_game, normalize_standings, records_from_standings

    games = api.get("/score/now").raise_for_status().json().get("games") or []
    if not games:
        pytest.skip("no games on the slate today")
    records = records_from_standings(normalize_standings(api.get("/standings/now").raise_for_status().json()))
    assert records, "standings produced no team records"
    for raw in games:
        game = normalize_game(raw, records)
        assert game["id"] and game["away"]["abbrev"] and game["home"]["abbrev"]
        assert game["phase"] in ("pregame", "live", "intermission", "postgame")
        assert game["away"]["record"], f"{game['away']['abbrev']} lost its record — the abbrevs stopped matching standings"
