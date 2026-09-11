"""What the NHL feed has to keep providing for the panel to be *right*.

`api-web.nhle.com` is undocumented and unversioned, and `normalize.py` is deliberately
forgiving — `_text()`, `or {}`, `int(x or 0)`. That is the correct shape for one flaky
response and the wrong shape for a permanent rename: if `situationCode` becomes something
else, nothing raises. `situation()` returns "ev", `outcome_label()` returns "", the board
renders, and the sign shows a confident, plausible, wrong scoreboard. Silent wrongness is
worse here than a crash, because a crash is quarantined and visibly falls back to the clock.

So the fields normalize actually reads are written down once, in `scoreboard/nhl/contract.py`,
and checked three ways:

  * against the recorded fixtures on every run — this proves the spec describes the real
    payloads rather than what someone assumed they contained;
  * at runtime, by the source, which reports every mismatch as feed drift on the diagnostics
    page (see `SourceContext.drift`);
  * against the live API when asked, which is the part that catches drift before a game night:

        SCOREBOARD_CONTRACT_TEST=1 uv run pytest tests/test_nhl_contract.py -q

Run the live pass on a schedule (`.github/workflows/nhl-contract.yml` does, weekly), not in the
fast suite. It needs the network, and during the off-season the score feed legitimately carries
no games — that is reported as a skip, not a pass, so an empty slate can never be mistaken for
a green contract.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from scoreboard.nhl.contract import (
    check_game,
    check_landing,
    check_score_payload,
    check_standings_payload,
)

FIX = Path(__file__).parent / "fixtures" / "nhl"
BASE = "https://api-web.nhle.com/v1"
LIVE = os.environ.get("SCOREBOARD_CONTRACT_TEST") == "1"
live_only = pytest.mark.skipif(not LIVE, reason="set SCOREBOARD_CONTRACT_TEST=1 to check the live API")


def _fixture(name: str) -> dict:
    return json.loads((FIX / name).read_text())


# -- the checks say something useful when a shape moves ------------------------

def test_check_game_names_what_moved():
    game = _fixture("score_2026-04-11.json")["games"][0]
    assert check_game(game) == []
    assert check_game({**game, "gameState": "WEIRD"}) == ["unknown gameState 'WEIRD'"]
    assert "missing gameDate" in " ".join(check_game({k: v for k, v in game.items() if k != "gameDate"}))
    assert "unknown periodType 'Q'" in check_game({**game, "periodDescriptor": {**game["periodDescriptor"], "periodType": "Q"}})
    assert "unknown gameScheduleState 'LATER'" in check_game({**game, "gameScheduleState": "LATER"})
    started_no_period = {**game, "gameState": "LIVE", "periodDescriptor": None}
    assert any("periodDescriptor" in n for n in check_game(started_no_period))
    live_no_clock = {**game, "gameState": "LIVE", "clock": {}}
    assert any("clock" in n for n in check_game(live_no_clock))
    assert any("awayTeam" in n for n in check_game({**game, "awayTeam": {"abbrev": "TOR"}}))   # started game, no score


def test_check_game_tolerates_a_game_that_has_not_started():
    game = _fixture("score_2026-04-11.json")["games"][0]
    future = {**game, "gameState": "FUT", "awayTeam": {"abbrev": "TOR"}, "homeTeam": {"abbrev": "FLA"}}
    future.pop("periodDescriptor", None)
    future.pop("clock", None)
    assert check_game(future) == []


def test_check_landing_guards_the_situation_code():
    landing = _fixture("landing_2025021270.json")
    assert check_landing(landing) == []
    assert any("situationCode" in n for n in check_landing({**landing, "situation": {"timeRemaining": "01:00"}}))
    assert any("4 digits" in n for n in check_landing({**landing, "situation": {"situationCode": "15"}}))


def test_check_score_payload_reports_a_missing_games_key():
    assert check_score_payload({}) == ["score payload has no 'games' key"]


# -- the spec describes the captures (runs offline, every time) -----------------

def test_spec_matches_the_recorded_score_feed():
    payload = _fixture("score_2026-04-11.json")
    assert payload["games"], "fixture has no games"
    assert check_score_payload(payload) == []


def test_spec_matches_the_recorded_landing_feed():
    assert check_landing(_fixture("landing_2025021270.json")) == []


def test_spec_matches_the_recorded_standings():
    assert check_standings_payload(_fixture("standings_2026-04-10.json")) == []


# -- and the live API still matches the spec (opt-in) ---------------------------

@pytest.fixture(scope="module")
def api() -> httpx.Client:
    with httpx.Client(base_url=BASE, timeout=20, follow_redirects=True,
                      headers={"User-Agent": "nhl-scoreboard-contract-test"}) as client:
        yield client


@live_only
def test_live_score_feed_still_has_what_the_boards_read(api):
    payload = api.get("/score/now").raise_for_status().json()
    assert check_score_payload(payload) == []
    if not payload.get("games"):
        pytest.skip("no games on the slate today — an empty slate proves nothing either way")


@live_only
def test_live_landing_feed_still_has_what_the_boards_read(api):
    games = api.get("/score/now").raise_for_status().json().get("games") or []
    played = [g for g in games if g.get("gameState") in ("LIVE", "CRIT", "OVER", "FINAL", "OFF")]
    if not played:
        pytest.skip("no game has started today; the landing feed has nothing to check yet")
    landing = api.get(f"/gamecenter/{played[0]['id']}/landing").raise_for_status().json()
    assert check_landing(landing) == [], f"live landing {played[0]['id']}"


@live_only
def test_live_standings_still_has_what_the_boards_read(api):
    assert check_standings_payload(api.get("/standings/now").raise_for_status().json()) == []


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
