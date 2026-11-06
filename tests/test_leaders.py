"""Leaders: the league's lists and a club's normalised to one shape, fetched on their own loop, drawn a category per page."""
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
from scoreboard.nhl.api import BASE_URL, NhlApi
from scoreboard.nhl.boards.leaders import (
    HEADER_H,
    Layout,
    LeadersBoard,
    LeadersConfig,
    TeamLeadersBoard,
    TeamLeadersConfig,
    goalie_columns,
    grown,
    names,
    stat_text,
)
from scoreboard.nhl.leaders import (
    KEEP,
    has_leaders,
    normalize_league_leaders,
    normalize_team_leaders,
)
from scoreboard.nhl.source import NhlConfig, NhlSource
from scoreboard.render.profiles import profile_for
from scoreboard.render.text import bitmap_safe, load_font

FIX = Path(__file__).parent / "fixtures" / "nhl"
TORONTO = ZoneInfo("America/Toronto")
SKATERS_URL = f"{BASE_URL}/skater-stats-leaders/current"
GOALIES_URL = f"{BASE_URL}/goalie-stats-leaders/current"


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def skaters():
    return load("skater_leaders_2026-11-06.json")


@pytest.fixture
def goalies():
    return load("goalie_leaders_2026-11-06.json")


@pytest.fixture
def club():
    return load("club_stats_TOR_2026-11-06.json")


# -- the feeds -> one shape ----------------------------------------------------------


def test_league_lists_keep_the_feeds_order(skaters, goalies):
    value = normalize_league_leaders({**skaters, "season": "20262027", "gameType": 2}, goalies)
    assert value["season"] == "20262027" and value["game_type"] == 2
    assert [(r["last"], r["team"], r["value"]) for r in value["points"][:3]] == [("McDavid", "EDM", 44), ("Draisaitl", "EDM", 31), ("Bouchard", "EDM", 30)]
    assert value["goals"][0] == {"id": 8478864, "first": "Kirill", "last": "Kaprizov", "team": "MIN", "position": "L", "value": 15}
    assert all(len(value[c]) == KEEP for c in ("goals", "assists", "points"))


def test_a_league_goalie_carries_his_whole_line_in_every_list(skaters, goalies):
    lists = normalize_league_leaders(skaters, goalies)["goalies"]
    assert list(lists) == ["wins", "save_pct", "gaa", "shutouts"] and all(len(rows) == KEEP for rows in lists.values())
    assert [r["last"] for r in lists["wins"][:2]] == ["Wallstedt", "Kochetkov"]
    assert [r["last"] for r in lists["save_pct"][:2]] == ["Ullmark", "Tarasov"]
    assert [r["last"] for r in lists["gaa"][:2]] == ["Ullmark", "Kochetkov"]            # lowest first, as the feed ranks it
    ullmark = lists["save_pct"][0]
    assert ullmark == {"id": 8476999, "first": "Linus", "last": "Ullmark", "team": "OTT", "wins": 7,
                       "save_pct": pytest.approx(0.945055), "gaa": pytest.approx(1.333136), "shutouts": 0}
    assert ullmark is lists["gaa"][0]                                                   # one line, whichever list


def test_a_goalie_the_league_does_not_rank_has_no_rate_numbers():
    feed = {"wins": [{"id": 1, "firstName": {"default": "A"}, "lastName": {"default": "Backup"}, "teamAbbrev": "TOR", "value": 1}],
            "savePctg": [], "goalsAgainstAverage": [], "shutouts": []}
    row = normalize_league_leaders(None, feed)["goalies"]["wins"][0]
    assert row["wins"] == 1 and row["save_pct"] is None and row["gaa"] is None and row["shutouts"] == 0


def test_league_normalise_shrugs_at_missing_halves_and_junk():
    empty = normalize_league_leaders(None, None)
    assert empty["goals"] == [] and empty["goalies"]["wins"] == [] and empty["season"] is None and not has_leaders(empty)
    odd = normalize_league_leaders({"goals": ["junk", {"id": 3, "lastName": "Plain", "value": "7"}], "assists": None}, {"wins": "nope"})
    assert odd["goals"] == [{"id": 3, "first": "", "last": "Plain", "team": "", "position": "", "value": 7}]
    assert odd["assists"] == [] and has_leaders(odd)
    assert not has_leaders(None)


def test_team_lists_are_ranked_here(club):
    value = normalize_team_leaders(club, "tor")
    assert value["season"] == "20262027" and value["game_type"] == 2
    assert [(r["last"], r["value"]) for r in value["goals"][:3]] == [("Matthews", 11), ("Nylander", 10), ("Roslovic", 8)]
    assert [(r["last"], r["value"]) for r in value["assists"][:2]] == [("Matthews", 15), ("Rielly", 12)]
    assert value["points"][0] == {"id": 8479318, "first": "Auston", "last": "Matthews", "team": "TOR", "position": "C", "value": 26}
    assert [r["value"] for r in value["points"]] == sorted((r["value"] for r in value["points"]), reverse=True)
    assert [(r["last"], r["wins"]) for r in value["goalies"]["wins"]] == [("Stolarz", 5), ("Bobrovsky", 3)]
    assert value["goalies"]["gaa"][0]["gaa"] < value["goalies"]["gaa"][1]["gaa"]
    assert "_gp" not in value["goalies"]["wins"][0]


def test_team_ties_and_zeros():
    def skater(name, g, a, gp=10):
        return {"playerId": hash(name), "firstName": {"default": name[0]}, "lastName": {"default": name}, "positionCode": "C",
                "gamesPlayed": gp, "goals": g, "assists": a, "points": g + a}

    def goalie(name, gp, wins, sv, gaa):
        return {"playerId": hash(name), "firstName": {"default": name[0]}, "lastName": {"default": name}, "gamesPlayed": gp,
                "wins": wins, "savePercentage": sv, "goalsAgainstAverage": gaa, "shutouts": 0}

    club = {"skaters": [skater("Passer", 0, 9), skater("Sniper", 5, 1), skater("Twoway", 5, 4), skater("Quick", 5, 4, gp=6), skater("Scratch", 0, 0)],
            "goalies": [goalie("Starter", 12, 7, 0.91, 2.5), goalie("Backup", 3, 1, 0.93, 2.1), goalie("Third", 0, 0, None, None)]}
    value = normalize_team_leaders(club, "TOR")
    assert [r["last"] for r in value["goals"]] == ["Quick", "Twoway", "Sniper"]         # level on goals: more points, then fewer games; no goals, no place
    assert [r["last"] for r in value["points"]] == ["Quick", "Twoway", "Passer", "Sniper"]
    assert [r["last"] for r in value["goalies"]["wins"]] == ["Starter", "Backup"]       # the third goalie has not played
    assert [r["last"] for r in value["goalies"]["save_pct"]] == ["Backup", "Starter"]
    assert [r["last"] for r in value["goalies"]["shutouts"]] == ["Starter", "Backup"]   # nobody has one: wins break the tie
    assert not has_leaders(normalize_team_leaders({"skaters": [skater("Scratch", 0, 0)], "goalies": []}, "TOR"))


# -- the client ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leaders_season_and_game_type_come_from_the_redirect(skaters):
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(SKATERS_URL).mock(return_value=httpx.Response(307, headers={"location": f"{BASE_URL}/skater-stats-leaders/20252026/3?categories=goals&limit=10"}))
        landed = mock.get(f"{BASE_URL}/skater-stats-leaders/20252026/3").mock(return_value=httpx.Response(200, json=skaters))
        data = await NhlApi(http).skater_leaders(("goals",), 10)
    assert data["season"] == "20252026" and data["gameType"] == 3 and landed.called
    assert normalize_league_leaders(data, None)["game_type"] == 3


# -- the source ---------------------------------------------------------------------


def _mock_basics(mock):
    mock.get(f"{BASE_URL}/score/now").mock(return_value=httpx.Response(200, json=load("score_2026-04-11.json")))
    mock.get(f"{BASE_URL}/standings/now").mock(return_value=httpx.Response(200, json=load("standings_2026-04-10.json")))
    mock.get(f"{BASE_URL}/club-schedule-season/TOR/now").mock(return_value=httpx.Response(200, json=load("club_schedule_TOR_week.json")))
    mock.get(f"{BASE_URL}/schedule/now").mock(return_value=httpx.Response(200, json=load("schedule_now.json")))
    mock.get(url__regex=rf"{BASE_URL}/schedule/\d{{4}}-\d\d-\d\d").mock(return_value=httpx.Response(200, json={"gameWeek": []}))


async def _run_until(store, cfg_ref, http, ready, rounds=300):
    ctx = SourceContext("nhl", store, lambda: cfg_ref[0], http)
    task = asyncio.create_task(NhlSource().run(ctx))
    for _ in range(rounds):
        await asyncio.sleep(0.01)
        if ready(store.get()):
            break
    return ctx, task


@pytest.fixture
def quiet_source(monkeypatch):
    import scoreboard.nhl.api as api
    import scoreboard.nhl.source as src
    monkeypatch.setattr(src, "_local_today", lambda ctx: "2026-04-11")
    monkeypatch.setattr(src, "carry_last_night", lambda ctx: False)
    monkeypatch.setattr(src, "LEADERS_OFF_SECONDS", 0.02)
    monkeypatch.setattr(api, "RETRY_DELAYS", ())
    return src


@pytest.mark.asyncio
async def test_source_publishes_league_and_team_leaders(quiet_source, skaters, goalies, club):
    store = SnapshotStore()
    cfg = [NhlConfig(favorites=["TOR"], goal_replays=False, game_stats=False)]
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        _mock_basics(mock)
        sk = mock.get(SKATERS_URL).mock(return_value=httpx.Response(200, json=skaters))
        go = mock.get(GOALIES_URL).mock(return_value=httpx.Response(200, json=goalies))
        cl = mock.get(f"{BASE_URL}/club-stats/TOR/now").mock(return_value=httpx.Response(200, json=club))
        _, task = await _run_until(store, cfg, http, lambda s: s.get("nhl.team_leaders"))
        task.cancel()
        snap = store.get()
        asked = dict(sk.calls.last.request.url.params), dict(go.calls.last.request.url.params)
    assert asked == ({"categories": "goals,assists,points", "limit": "10"},
                     {"categories": "wins,savePctg,goalsAgainstAverage,shutouts", "limit": "-1"})
    assert snap.get("nhl.leaders")["points"][0]["last"] == "McDavid" and snap.get("nhl.leaders")["goalies"]["wins"][0]["last"] == "Wallstedt"
    assert list(snap.get("nhl.team_leaders")) == ["TOR"] and snap.get("nhl.team_leaders")["TOR"]["goals"][0]["last"] == "Matthews"
    assert (sk.call_count, go.call_count, cl.call_count) == (1, 1, 1)


@pytest.mark.asyncio
async def test_source_leaders_off_fetches_nothing_and_withdraws(quiet_source, skaters, goalies, club):
    store = SnapshotStore()
    cfg = [NhlConfig(favorites=["TOR"], goal_replays=False, game_stats=False, leaders=False)]
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        _mock_basics(mock)
        sk = mock.get(SKATERS_URL).mock(return_value=httpx.Response(200, json=skaters))
        mock.get(GOALIES_URL).mock(return_value=httpx.Response(200, json=goalies))
        mock.get(f"{BASE_URL}/club-stats/TOR/now").mock(return_value=httpx.Response(200, json=club))
        ctx, task = await _run_until(store, cfg, http, lambda s: s.has("nhl.main_event"))
        await asyncio.sleep(0.05)
        assert sk.call_count == 0 and not store.get().has("nhl.leaders")
        cfg[0] = cfg[0].model_copy(update={"leaders": True})                    # switched on: fetched without a restart
        for _ in range(200):
            await asyncio.sleep(0.01)
            if store.get().get("nhl.team_leaders"):
                break
        assert store.get().get("nhl.leaders") and store.get().get("nhl.team_leaders")
        cfg[0] = cfg[0].model_copy(update={"leaders": False})                   # and off again: both keys withdrawn
        ctx.wake()
        for _ in range(200):
            await asyncio.sleep(0.01)
            if store.get().get("nhl.leaders") is None:
                break
        task.cancel()
    assert store.get().get("nhl.leaders") is None and store.get().get("nhl.team_leaders") is None


@pytest.mark.asyncio
async def test_a_failed_fetch_keeps_what_was_there(quiet_source, skaters, goalies, club):
    store = SnapshotStore()
    cfg = [NhlConfig(favorites=["TOR", "MTL"], goal_replays=False, game_stats=False)]
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        _mock_basics(mock)
        mock.get(f"{BASE_URL}/club-schedule-season/MTL/now").mock(return_value=httpx.Response(200, json=load("club_schedule_TOR_week.json")))
        mock.get(SKATERS_URL).mock(return_value=httpx.Response(200, json=skaters))
        go = mock.get(GOALIES_URL).mock(return_value=httpx.Response(200, json=goalies))
        tor = mock.get(f"{BASE_URL}/club-stats/TOR/now").mock(return_value=httpx.Response(200, json=club))
        mock.get(f"{BASE_URL}/club-stats/MTL/now").mock(return_value=httpx.Response(500))      # one club down: the other still shows
        ctx, task = await _run_until(store, cfg, http, lambda s: s.get("nhl.team_leaders"))
        assert list(store.get().get("nhl.team_leaders")) == ["TOR"]
        go.mock(return_value=httpx.Response(500))
        tor.mock(return_value=httpx.Response(500))
        rounds = go.call_count
        ctx.wake()                                                                             # the next round, now
        for _ in range(200):
            await asyncio.sleep(0.01)
            if go.call_count > rounds and tor.call_count > 1:
                break
        await asyncio.sleep(0.05)
        task.cancel()
    snap = store.get()
    assert snap.get("nhl.leaders")["goalies"]["wins"][0]["last"] == "Wallstedt"                # the half that failed keeps its lists
    assert snap.get("nhl.leaders")["goals"][0]["last"] == "Kaprizov"
    assert snap.get("nhl.team_leaders")["TOR"]["goals"][0]["last"] == "Matthews"


# -- the boards ---------------------------------------------------------------------


@pytest.fixture
def snapshot(skaters, goalies, club):
    store = SnapshotStore()
    store.publish("nhl.team_summary", {"TOR": {}})
    store.publish("nhl.leaders", normalize_league_leaders(skaters, goalies))
    return store.publish("nhl.team_leaders", {"TOR": normalize_team_leaders(club, "TOR"), "MTL": normalize_team_leaders(club, "MTL")})


def ctx_for(snap, size=(128, 64), elapsed=2.0, pace=None):
    w, h = size
    return BoardContext(snap, profile_for(w, h), w, h, 30, datetime(2026, 11, 6, 18, 30, tzinfo=TORONTO), elapsed, pace=pace)


def test_a_page_per_category_each_with_its_own_switch(snapshot):
    board, ctx = LeadersBoard(), ctx_for(snapshot)
    assert [(p.team, p.category, len(p.rows)) for p in board.pages(ctx, LeadersConfig())] == [
        (None, "goals", 5), (None, "assists", 5), (None, "points", 5), (None, "goalies", 5)]
    assert [p.category for p in board.pages(ctx, LeadersConfig(assists=False, goalies=False))] == ["goals", "points"]
    assert [p.category for p in board.pages(ctx, LeadersConfig(goals=False, assists=False, points=False))] == ["goalies"]
    off = LeadersConfig(goals=False, assists=False, points=False, goalies=False)
    assert board.pages(ctx, off) == [] and board.done(ctx, off) and board.auto_seconds(ctx, off) is None and board.auto_items(ctx, off) is None
    assert board.render(ctx, off).getbbox() is None


def test_team_board_runs_each_favourite_in_turn_with_its_own_switches(snapshot):
    board, ctx = TeamLeadersBoard(), ctx_for(snapshot)
    pages = board.pages(ctx, TeamLeadersConfig(assists=False))
    assert [(p.team, p.category) for p in pages] == [("TOR", "goals"), ("TOR", "points"), ("TOR", "goalies"),
                                                     ("MTL", "goals"), ("MTL", "points"), ("MTL", "goalies")]
    assert len(pages[2].rows) == 2                                                      # a club has the goalies it has
    assert TeamLeadersConfig.model_json_schema()["title"] == "NHL team leaders" and LeadersConfig.model_json_schema()["title"] == "NHL league leaders"


def test_count_is_capped_by_what_the_panel_fits(snapshot):
    board = LeadersBoard()
    assert len(board.pages(ctx_for(snapshot), LeadersConfig(count=10))[0].rows) == 8
    assert len(board.pages(ctx_for(snapshot, (64, 32)), LeadersConfig())[0].rows) == 3
    assert len(board.pages(ctx_for(snapshot), LeadersConfig(count=2))[0].rows) == 2
    assert Layout(128, 64).pitch(5) == 11 and Layout(128, 64).pitch(8) == 7 and Layout(128, 32).pitch(3) == 8 and Layout(128, 64).pitch(1) == 11


def test_goalies_are_ranked_by_the_setting(snapshot):
    board, ctx = LeadersBoard(), ctx_for(snapshot)
    only = {"goals": False, "assists": False, "points": False}
    assert board.pages(ctx, LeadersConfig(**only))[0].rows[0]["last"] == "Wallstedt"
    assert board.pages(ctx, LeadersConfig(**only, goalie_rank="save_pct"))[0].rows[0]["last"] == "Ullmark"
    assert board.pages(ctx, LeadersConfig(**only, goalie_rank="shutouts"))[0].rows[0]["last"] == "Oettinger"
    assert goalie_columns("wins", True) == ("wins", "gaa", "save_pct") and goalie_columns("shutouts", True) == ("shutouts", "gaa", "save_pct")
    assert goalie_columns("gaa", False) == ("gaa",)


def test_pages_turn_on_the_playlists_seconds(snapshot):
    board, cfg = LeadersBoard(), LeadersConfig()
    assert board.auto_seconds(ctx_for(snapshot), cfg) == 32.0 and board.auto_items(ctx_for(snapshot), cfg) == (4, "page")
    assert board.auto_seconds(ctx_for(snapshot, pace=5.0), cfg) == 20.0
    assert not board.done(ctx_for(snapshot, elapsed=31.9), cfg) and board.done(ctx_for(snapshot, elapsed=32.0), cfg)
    goals, assists = board.render(ctx_for(snapshot, elapsed=2.0), cfg), board.render(ctx_for(snapshot, elapsed=10.0), cfg)
    assert goals.tobytes() != assists.tobytes()
    assert board.render(ctx_for(snapshot, elapsed=3.0, pace=2.0), cfg).tobytes() == board.render(ctx_for(snapshot, elapsed=9.0), cfg).tobytes() != goals.tobytes()


@pytest.mark.parametrize("size", [(128, 64), (64, 32), (128, 32), (64, 64), (128, 128), (192, 128), (256, 256)])
@pytest.mark.parametrize("board, cfg", [(LeadersBoard(), LeadersConfig(count=10)), (TeamLeadersBoard(), TeamLeadersConfig(count=10, goalie_rank="gaa"))])
def test_every_page_draws_at_every_size(snapshot, size, board, cfg):
    for index in range(len(board.pages(ctx_for(snapshot, size), cfg))):
        img = board.render(ctx_for(snapshot, size, elapsed=index * cfg.seconds_per_page + 2.0), cfg)
        assert img.size == size and img.getbbox() is not None
        assert img.crop((0, HEADER_H, size[0], HEADER_H + 1)).getbbox() is None             # the dark line under the header


def test_header_is_white_for_the_league_and_the_clubs_colour_for_a_club(snapshot):
    from scoreboard.nhl.teams import team
    assert LeadersBoard().render(ctx_for(snapshot), LeadersConfig()).getpixel((127, 0)) == (255, 255, 255)
    assert TeamLeadersBoard().render(ctx_for(snapshot), TeamLeadersConfig()).getpixel((127, 0)) == team("TOR").primary


def test_bars_grow_from_the_left_and_only_under_counts(snapshot):
    assert grown(0.8, 0.0, 0, True) == 0.0 and grown(0.8, 5.0, 4, True) == 0.8 and grown(0.8, 0.0, 0, False) == 0.8
    assert 0 < grown(0.8, 0.3, 0, True) < 0.8 and grown(0.8, 0.3, 3, True) < grown(0.8, 0.3, 0, True)
    board, cfg = LeadersBoard(), LeadersConfig()
    lit = lambda img, y: sum(1 for x in range(128) if img.getpixel((x, y)) not in ((0, 0, 0), (36, 36, 36)))   # noqa: E731
    bar_y = 9 + 5 + 1                                                                    # first row's text, a gap, its bar
    early, settled = board.render(ctx_for(snapshot, elapsed=0.2), cfg), board.render(ctx_for(snapshot, elapsed=2.0), cfg)
    assert 0 < lit(early, bar_y) < lit(settled, bar_y) == 128 - 2 - 29 + 1               # the leader's bar runs the whole track
    still = LeadersConfig(animate=False)
    assert board.render(ctx_for(snapshot, elapsed=0.0), still).tobytes() == board.render(ctx_for(snapshot, elapsed=2.0), still).tobytes()
    rates = LeadersConfig(goals=False, assists=False, points=False, goalie_rank="save_pct")
    assert lit(board.render(ctx_for(snapshot), rates), bar_y) == 0 and board.render(ctx_for(snapshot), rates).getpixel((40, bar_y)) == (0, 0, 0)


def test_names_take_an_initial_when_the_whole_page_fits_one():
    font = load_font("pixel", 6)
    rows = ({"first": "Connor", "last": "McDavid"}, {"first": "Tim", "last": "Stützle"}, {"first": "Jakub", "last": "Dobeš"})
    assert names(rows, 80, font) == ["C. MCDAVID", "T. STÜTZLE", "J. DOBES"]
    assert names(rows, 30, font) == ["MCDAVID", "STÜTZLE", "DOBES"]
    assert names(({"first": "Oliver", "last": "Ekman-Larsson"},), 35, font) == ["EKMAN-LAR"]
    assert names(({"first": "Oliver", "last": "Ekman-Larsson"},), 23, font) == ["EKMAN"]      # never ends on the hyphen
    assert names(({"last": "Solo"},), 80, font) == ["SOLO"]


def test_numbers_read_as_the_league_prints_them():
    assert stat_text("save_pct", 0.919897) == ".920" and stat_text("save_pct", 1.0) == "1.000" and stat_text("gaa", 1.333136) == "1.33"
    assert stat_text("wins", 10) == "10" and stat_text("gaa", None) == "-" and stat_text("save_pct", None) == "-"


def test_bitmap_safe_keeps_latin_1_and_folds_the_rest():
    assert bitmap_safe("Stützle") == "Stützle" and bitmap_safe("Dobeš") == "Dobes" and bitmap_safe("Jiříček") == "Jirícek"
    assert bitmap_safe("Slafkovský") == "Slafkovský" and bitmap_safe("plain") == "plain" and bitmap_safe("日本") == ""


def test_the_boards_sit_out_without_data_and_follow_the_season():
    from scoreboard.plugins import load_registry
    registry = load_registry()
    league, club = registry.boards["nhl.leaders"], registry.boards["nhl.team_leaders"]
    assert league.requires == {"nhl.leaders"} and club.requires == {"nhl.team_leaders"}
    assert league.seasonal and club.seasonal and league.pace_unit == club.pace_unit == "page"
