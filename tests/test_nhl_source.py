import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import ValidationError

from scoreboard.data import SnapshotStore
from scoreboard.data.health import SourceHealth
from scoreboard.data.source import SourceContext
from scoreboard.nhl.api import BASE_URL
from scoreboard.nhl.source import NhlConfig, NhlSource

FIX = Path(__file__).parent / "fixtures" / "nhl"


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.mark.asyncio
async def test_source_publishes_scores_main_event_standings_and_summary(monkeypatch):
    import scoreboard.nhl.source as src
    monkeypatch.setattr(src, "_local_today", lambda ctx: "2026-04-11")     # fixture game day
    store = SnapshotStore()
    cfg = NhlConfig(favorites=["TOR"], idle_interval=15, standings_interval=300)
    async with httpx.AsyncClient() as http, respx.mock(base_url=BASE_URL) as mock:
        mock.get("/score/now").mock(return_value=httpx.Response(200, json=load("score_2026-04-11.json")))
        mock.get("/standings/now").mock(return_value=httpx.Response(200, json=load("standings_2026-04-10.json")))
        mock.get("/club-schedule-season/TOR/now").mock(return_value=httpx.Response(200, json=load("club_schedule_TOR_week.json")))
        ctx = SourceContext("nhl", store, lambda: cfg, http)
        task = asyncio.create_task(NhlSource().run(ctx))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if store.get().has("nhl.scores", "nhl.main_event", "nhl.standings", "nhl.team_summary"):
                break
        task.cancel()
        snap = store.get()
    assert len(snap.get("nhl.scores")) == 15
    assert snap.get("nhl.main_event")["home"]["abbrev"] == "TOR" and snap.get("nhl.main_event")["phase"] == "postgame"
    assert snap.get("nhl.main_event")["home"]["record"].count("-") == 2   # scores waited for standings
    assert "TOR" in snap.get("nhl.team_summary")
    assert snap.get("system")["online"] is True


@pytest.mark.asyncio
async def test_source_marks_offline_on_failure(monkeypatch):
    store = SnapshotStore()
    cfg = NhlConfig(favorites=["TOR"])
    import scoreboard.nhl.api as api_mod
    import scoreboard.nhl.source as src
    api_mod.RETRY_DELAYS = (0, 0, 0)
    monkeypatch.setattr(src, "OFFLINE_AFTER_FAILURES", 1)       # threshold itself is covered below
    async with httpx.AsyncClient() as http, respx.mock(base_url=BASE_URL) as mock:
        mock.get("/score/now").mock(return_value=httpx.Response(503))
        mock.get("/standings/now").mock(return_value=httpx.Response(503))
        ctx = SourceContext("nhl", store, lambda: cfg, http)
        task = asyncio.create_task(NhlSource().run(ctx))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if store.get().has("system"):
                break
        task.cancel()
    assert store.get().get("system")["online"] is False


def test_offline_threshold_is_several_failures():
    from scoreboard.nhl.source import OFFLINE_AFTER_FAILURES
    assert OFFLINE_AFTER_FAILURES >= 3


# -- resilience: one bad game, unknown values, registry drift ----------------------

async def _run_until(store, keys, source_cfg, mock_setup):
    async with httpx.AsyncClient() as http, respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        mock_setup(mock)
        health = SourceHealth()
        ctx = SourceContext("nhl", store, lambda: source_cfg, http, health=health)
        task = asyncio.create_task(NhlSource().run(ctx))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if store.get().has(*keys):
                break
        task.cancel()
        return health.get("nhl")


def _standard(mock, score):
    mock.get("/score/now").mock(return_value=httpx.Response(200, json=score))
    mock.get("/standings/now").mock(return_value=httpx.Response(200, json=load("standings_2026-04-10.json")))
    mock.get("/club-schedule-season/TOR/now").mock(return_value=httpx.Response(200, json=load("club_schedule_TOR_week.json")))
    for unknown in ("QCN", "ZZZ"):
        mock.get(f"/club-schedule-season/{unknown}/now").mock(return_value=httpx.Response(404))
    mock.get("/schedule/now").mock(return_value=httpx.Response(200, json=load("schedule_now.json")))


@pytest.mark.asyncio
async def test_one_malformed_game_is_dropped_not_fatal(monkeypatch):
    import scoreboard.nhl.source as src
    monkeypatch.setattr(src, "_local_today", lambda ctx: "2026-04-11")
    score = load("score_2026-04-11.json")
    broken = {**score["games"][0], "awayTeam": None, "id": None}      # int(None) and None["abbrev"] both blow up
    score = {**score, "games": [broken, *score["games"][1:]]}
    store = SnapshotStore()
    stats = await _run_until(store, ("nhl.scores", "nhl.main_event"), NhlConfig(favorites=["TOR"], idle_interval=15),
                             lambda m: _standard(m, score))
    assert len(store.get().get("nhl.scores")) == 14
    assert store.get().get("nhl.main_event")["home"]["abbrev"] == "TOR"
    assert stats.restarts == 0
    assert any("could not be normalised" in n for n in stats.drift)


@pytest.mark.asyncio
async def test_unknown_feed_values_are_reported_as_drift(monkeypatch):
    import scoreboard.nhl.source as src
    monkeypatch.setattr(src, "_local_today", lambda ctx: "2026-04-11")
    score = load("score_2026-04-11.json")
    odd = {**score["games"][0], "gameState": "WEIRD"}
    score = {**score, "games": [odd, *score["games"][1:]]}
    store = SnapshotStore()
    stats = await _run_until(store, ("nhl.scores",), NhlConfig(favorites=["TOR"], idle_interval=15),
                             lambda m: _standard(m, score))
    assert len(store.get().get("nhl.scores")) == 15                     # the game is still published (as pregame)
    assert "unknown gameState 'WEIRD'" in stats.drift


@pytest.mark.asyncio
async def test_registry_and_favourite_mismatches_are_reported(monkeypatch):
    import scoreboard.nhl.source as src
    monkeypatch.setattr(src, "_local_today", lambda ctx: "2026-04-11")
    standings = load("standings_2026-04-10.json")
    new_team = {**standings["standings"][0], "teamAbbrev": {"default": "QCN"}}
    standings = {**standings, "standings": [new_team, *standings["standings"][1:]]}

    def setup(mock):
        _standard(mock, load("score_2026-04-11.json"))
        mock.get("/standings/now").mock(return_value=httpx.Response(200, json=standings))

    store = SnapshotStore()
    stats = await _run_until(store, ("nhl.standings", "nhl.team_summary"), NhlConfig(favorites=["TOR", "QCN", "ZZZ"]), setup)
    assert "QCN" in store.get().get("nhl.standings")["teams"]         # an unregistered team still gets a standings row
    assert "QCN" in store.get().get("nhl.team_summary")               # and a summary, since it is a favourite
    assert any("QCN" in n and "teams.py" in n for n in stats.drift)   # registry needs updating
    assert any("ZZZ" in n and "standings" in n for n in stats.drift)  # favourite the league does not know
    assert not any("TOR" in n for n in stats.drift)


# -- favourites accept a code the picker does not list ----------------------------

def test_favourites_accept_unknown_abbrev_uppercased():
    cfg = NhlConfig(favorites=["tor", " qcn "])
    assert cfg.favorites == ["TOR", "QCN"]


@pytest.mark.parametrize("bad", ["", "T", "TORONTO", "T0R", "to-r"])
def test_favourites_reject_things_that_are_not_abbrevs(bad):
    with pytest.raises(ValidationError):
        NhlConfig(favorites=[bad])


def test_favourites_schema_still_lists_the_teams_for_the_picker():
    from scoreboard.nhl.teams import NHL_TEAMS

    items = NhlConfig.model_json_schema()["properties"]["favorites"]["items"]
    assert items.get("enum") == list(NHL_TEAMS)
