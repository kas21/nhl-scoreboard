"""Game stats: the right rail normalised to rows with a share, fetched while the game is on, drawn as tug-of-war bars."""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from scoreboard.boards.base import BoardContext
from scoreboard.data import SnapshotStore
from scoreboard.data.source import SourceContext
from scoreboard.nhl.api import BASE_URL
from scoreboard.nhl.boards.stats import (
    HEADER_H,
    GameStatsBoard,
    GameStatsConfig,
    Layout,
    pages_of,
    split_at,
)
from scoreboard.nhl.source import NhlConfig, NhlSource
from scoreboard.nhl.stats import normalize_game_stats, stats_candidate
from scoreboard.render.profiles import profile_for

FIX = Path(__file__).parent / "fixtures" / "nhl"
GAME = 2025021270
TORONTO = ZoneInfo("America/Toronto")


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def rail():
    return load("right_rail_2025021270.json")


# -- the feed -> rows ----------------------------------------------------------------


def test_normalise_every_category_with_text_and_share(rail):
    value = normalize_game_stats(rail, GAME, "FLA", "TOR")
    assert value["game_id"] == GAME and value["away"] == {"abbrev": "FLA", "score": 6} and value["home"] == {"abbrev": "TOR", "score": 2}
    rows = {r["key"]: r for r in value["stats"]}
    assert list(rows) == ["sog", "faceoffWinningPctg", "hits", "blockedShots", "pim", "powerPlay", "giveaways", "takeaways"]
    assert rows["sog"]["away"] == "25" and rows["sog"]["home"] == "19" and rows["sog"]["share"] == pytest.approx(25 / 44, abs=1e-3)
    assert rows["faceoffWinningPctg"]["away"] == "36%" and rows["faceoffWinningPctg"]["share"] == pytest.approx(0.357, abs=1e-3)
    assert rows["faceoffWinningPctg"]["detail"] == ["20/56", "36/56"]
    assert rows["powerPlay"]["away"] == "0/1" and rows["powerPlay"]["home"] == "1/1" and rows["powerPlay"]["share"] == 0.0
    assert rows["powerPlay"]["detail"] == ["0%", "100%"]
    assert rows["pim"]["share"] == 0.5                                        # 2 and 2
    assert rows["hits"]["label"] == "HITS" and rows["blockedShots"]["short"] == "BLK"
    assert [(r["label"], r["away"], r["home"]) for r in value["shots_by_period"]] == [("SHOTS 1ST", "8", "6"), ("SHOTS 2ND", "7", "6"), ("SHOTS 3RD", "10", "7")]
    assert [r["label"] for r in value["goals_by_period"]] == ["GOALS 1ST", "GOALS 2ND", "GOALS 3RD"]


def test_normalise_shrugs_at_an_empty_or_odd_rail():
    value = normalize_game_stats({}, 1, "A", "B")
    assert value["stats"] == [] and value["shots_by_period"] == [] and value["away"]["score"] == 0
    rail = {"teamGameStats": [{"category": "sog", "awayValue": None, "homeValue": "7"}, {"category": "powerPlay", "awayValue": "x", "homeValue": 3},
                              {"category": "mystery", "awayValue": 1, "homeValue": 2}, "junk"],
            "shotsByPeriod": [{"periodDescriptor": {"number": 4, "periodType": "OT"}, "away": 1, "home": 0},
                              {"periodDescriptor": {"number": 5, "periodType": "OT", "maxRegulationPeriods": 3}, "away": 0, "home": 0}]}
    value = normalize_game_stats(rail, 1, "A", "B")
    assert [(r["key"], r["away"], r["home"], r["share"]) for r in value["stats"]] == [("sog", "0", "7", 0.0), ("powerPlay", "x", "3", 0.5)]
    assert [(r["label"], r["share"]) for r in value["shots_by_period"]] == [("SHOTS OT", 1.0), ("SHOTS 2OT", 0.5)]


def test_candidate_is_the_main_event_on_or_over():
    store = SnapshotStore()
    game = {"id": 5, "phase": "live", "away": {"abbrev": "FLA"}, "home": {"abbrev": "TOR"}}
    assert stats_candidate(store.publish("nhl.main_event", game)) == {"id": 5, "phase": "live", "away": "FLA", "home": "TOR"}
    assert stats_candidate(store.publish("nhl.main_event", {**game, "phase": "postgame"}))["phase"] == "postgame"
    assert stats_candidate(store.publish("nhl.main_event", {**game, "phase": "pregame"})) is None
    assert stats_candidate(store.publish("nhl.main_event", {**game, "simulated": True})) is None
    assert stats_candidate(store.publish("nhl.main_event", None)) is None


# -- the source ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_publishes_stats_for_the_favourites_game(monkeypatch, rail):
    import scoreboard.nhl.source as src
    monkeypatch.setattr(src, "_local_today", lambda ctx: "2026-04-11")
    monkeypatch.setattr(src, "carry_last_night", lambda ctx: False)
    monkeypatch.setattr(src, "REPLAY_LOOKUP_SECONDS", 0.02)
    store = SnapshotStore()
    cfg = NhlConfig(favorites=["TOR"], idle_interval=15, standings_interval=300, goal_replays=False)
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/score/now").mock(return_value=httpx.Response(200, json=load("score_2026-04-11.json")))
        mock.get(f"{BASE_URL}/standings/now").mock(return_value=httpx.Response(200, json=load("standings_2026-04-10.json")))
        mock.get(f"{BASE_URL}/club-schedule-season/TOR/now").mock(return_value=httpx.Response(200, json=load("club_schedule_TOR_week.json")))
        mock.get(f"{BASE_URL}/schedule/now").mock(return_value=httpx.Response(200, json=load("schedule_now.json")))
        mock.get(url__regex=rf"{BASE_URL}/schedule/\d{{4}}-\d\d-\d\d").mock(return_value=httpx.Response(200, json={"gameWeek": []}))
        right_rail = mock.get(f"{BASE_URL}/gamecenter/{GAME}/right-rail").mock(return_value=httpx.Response(200, json=rail))
        ctx = SourceContext("nhl", store, lambda: cfg, http)
        task = asyncio.create_task(NhlSource().run(ctx))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if store.get().get("nhl.game_stats"):
                break
        task.cancel()
        snap = store.get()
    value = snap.get("nhl.game_stats")
    assert value["game_id"] == GAME and value["away"]["abbrev"] == "FLA" and len(value["stats"]) == 8
    assert right_rail.call_count == 1


@pytest.mark.asyncio
async def test_source_stats_off(monkeypatch, rail):
    import scoreboard.nhl.source as src
    monkeypatch.setattr(src, "_local_today", lambda ctx: "2026-04-11")
    monkeypatch.setattr(src, "carry_last_night", lambda ctx: False)
    monkeypatch.setattr(src, "REPLAY_LOOKUP_SECONDS", 0.02)
    store = SnapshotStore()
    cfg = NhlConfig(favorites=["TOR"], idle_interval=15, standings_interval=300, goal_replays=False, game_stats=False)
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/score/now").mock(return_value=httpx.Response(200, json=load("score_2026-04-11.json")))
        mock.get(f"{BASE_URL}/standings/now").mock(return_value=httpx.Response(200, json=load("standings_2026-04-10.json")))
        mock.get(f"{BASE_URL}/club-schedule-season/TOR/now").mock(return_value=httpx.Response(200, json=load("club_schedule_TOR_week.json")))
        mock.get(f"{BASE_URL}/schedule/now").mock(return_value=httpx.Response(200, json=load("schedule_now.json")))
        mock.get(url__regex=rf"{BASE_URL}/schedule/\d{{4}}-\d\d-\d\d").mock(return_value=httpx.Response(200, json={"gameWeek": []}))
        right_rail = mock.get(f"{BASE_URL}/gamecenter/{GAME}/right-rail").mock(return_value=httpx.Response(200, json=rail))
        ctx = SourceContext("nhl", store, lambda: cfg, http)
        task = asyncio.create_task(NhlSource().run(ctx))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if store.get().has("nhl.main_event"):
                break
        await asyncio.sleep(0.05)
        task.cancel()
    assert not store.get().has("nhl.game_stats") and right_rail.call_count == 0


# -- the board ----------------------------------------------------------------------


def _ctx(snap, w, h, elapsed, pace=None):
    return BoardContext(snapshot=snap, profile=profile_for(w, h), width=w, height=h, fps=30,
                        now=datetime(2026, 4, 11, 22, 0, tzinfo=TORONTO), elapsed=elapsed, pace=pace)


@pytest.fixture
def snap(rail):
    store = SnapshotStore()
    store.publish("main_event", {"id": GAME, "sport": "nhl", "phase": "intermission",
                                 "away": {"abbrev": "FLA", "score": 3}, "home": {"abbrev": "TOR", "score": 1}})
    return store.publish("nhl.game_stats", normalize_game_stats(rail, GAME, "FLA", "TOR"))


def test_pages_split_evenly():
    rows = [{"n": i} for i in range(11)]
    assert [len(p) for p in pages_of(rows, 5)] == [4, 4, 3]
    assert [len(p) for p in pages_of(rows, 3)] == [3, 3, 3, 2]
    assert [len(p) for p in pages_of(rows[:5], 5)] == [5] and pages_of([], 5) == []


def test_layout_fits_the_panel():
    for w, h in ((128, 64), (64, 32), (128, 32), (128, 128)):
        lay = Layout(w, h)
        pitch = lay.pitch(lay.rows_per_page)
        assert HEADER_H + (lay.rows_per_page - 1) * pitch + lay.bar_y + lay.bar_h <= h
    assert Layout(128, 64).rows_per_page == 5 and Layout(64, 32).rows_per_page == 3


def test_split_eases_from_the_middle_row_by_row():
    assert split_at(0.8, 0.0, 0, True) == 0.5
    assert split_at(0.8, 0.0, 3, True) == 0.5                                 # the fourth row has not started
    assert 0.5 < split_at(0.8, 0.2, 0, True) < 0.8
    assert split_at(0.8, 0.2, 0, True) > split_at(0.8, 0.2, 2, True)         # the top row leads
    assert split_at(0.8, 5.0, 0, True) == pytest.approx(0.8) and split_at(0.8, 0.0, 0, False) == 0.8


def test_board_pages_and_timing(snap):
    board, cfg = GameStatsBoard(), GameStatsConfig()
    ctx = _ctx(snap, 128, 64, 0.0)
    assert board.auto_items(ctx, cfg) == (3, "page") and board.auto_seconds(ctx, cfg) == 18.0       # 8 stats + 3 periods of shots: 4/4/3
    assert board.auto_items(_ctx(snap, 64, 32, 0.0), cfg) == (4, "page")
    assert board.auto_items(ctx, GameStatsConfig(show_period_shots=False)) == (2, "page")
    assert board.auto_items(ctx, GameStatsConfig(show_period_goals=True)) == (3, "page")             # 14 rows: 5/5/4
    assert board.auto_seconds(_ctx(snap, 128, 64, 0.0, pace=4.0), cfg) == 12.0
    assert not board.done(_ctx(snap, 128, 64, 17.9), cfg) and board.done(_ctx(snap, 128, 64, 18.0), cfg)


def test_board_draws_and_animates(snap):
    board, cfg = GameStatsBoard(), GameStatsConfig()
    for w, h in ((128, 64), (64, 32), (128, 32)):
        img = board.render(_ctx(snap, w, h, 2.0), cfg)
        assert img.size == (w, h) and img.getbbox()
    early = board.render(_ctx(snap, 128, 64, 0.05), cfg)
    settled = board.render(_ctx(snap, 128, 64, 2.0), cfg)
    assert early.tobytes() != settled.tobytes()                              # the splits are still sliding
    assert board.render(_ctx(snap, 128, 64, 3.0), cfg).tobytes() == settled.tobytes()       # and then they stand still
    assert board.render(_ctx(snap, 128, 64, 0.05), GameStatsConfig(animate=False)).tobytes() == settled.tobytes()
    page2 = board.render(_ctx(snap, 128, 64, 8.0), cfg)
    assert page2.tobytes() != settled.tobytes()
    assert settled.getpixel((64, 1)) in ((255, 255, 255), (0, 0, 0))         # header: the score from the main event sits in the middle
    row = settled.crop((0, HEADER_H, 128, HEADER_H + 5))
    assert row.getbbox()                                                      # values and label on the first row


def test_board_without_stats_is_blank_and_done():
    store = SnapshotStore()
    snap = store.publish("nhl.game_stats", {"game_id": 1, "away": {"abbrev": "A"}, "home": {"abbrev": "B"}, "stats": [], "shots_by_period": [], "goals_by_period": []})
    board, cfg = GameStatsBoard(), GameStatsConfig()
    ctx = _ctx(snap, 128, 64, 0.0)
    assert board.render(ctx, cfg).getbbox() is None and board.done(ctx, cfg) and board.auto_items(ctx, cfg) is None


def test_board_is_registered_and_in_the_intermission_and_postgame_rotations():
    from scoreboard.config.models import Playlists
    from scoreboard.plugins import load_registry
    assert "nhl.game_stats" in load_registry().boards
    assert "nhl.game_stats" in [e.board for e in Playlists().intermission]
    assert "nhl.game_stats" in [e.board for e in Playlists().postgame]
