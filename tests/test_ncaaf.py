import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from scoreboard import logos
from scoreboard.boards.base import BoardContext
from scoreboard.config.models import AppConfig
from scoreboard.data import Event, SnapshotStore
from scoreboard.data.source import SourceContext
from scoreboard.ncaaf.boards.game import NcaafGameBoard, NcaafGameConfig, with_ranks
from scoreboard.ncaaf.boards.others import (
    NcaafScoreBoard,
    NcaafScoreConfig,
    NcaafStandingsBoard,
    NcaafStandingsConfig,
    NcaafTeamSummaryBoard,
    NcaafTickerBoard,
)
from scoreboard.ncaaf.events import detect_ncaaf
from scoreboard.ncaaf.normalize import (
    normalize_scoreboard,
    normalize_standings,
    team_summary,
)
from scoreboard.ncaaf.source import NcaafConfig, NcaafSource, slate
from scoreboard.ncaaf.teams import CONFERENCE_OF, CONFERENCES, NCAAF_TEAMS
from scoreboard.nhl.boards.team_summary import TeamSummaryConfig
from scoreboard.nhl.boards.ticker import TickerConfig
from scoreboard.render.profiles import profile_for
from scoreboard.web.dashboard import dashboard_summary

FIX = Path(__file__).parent / "fixtures" / "ncaaf"
TODAY = "2026-09-05"


def load(n):
    return json.loads((FIX / n).read_text())


def test_registry_is_the_2026_fbs():
    assert len(NCAAF_TEAMS) == 136 and len(CONFERENCES) == 11
    assert CONFERENCE_OF["MICH"] == "Big Ten" and CONFERENCE_OF["TXST"] == "Pac-12" and CONFERENCE_OF["ND"] == "Independents"
    listed = {t["team"]["abbreviation"] for t in load("espn_teams.json")["sports"][0]["leagues"][0]["teams"]}
    assert listed == set(NCAAF_TEAMS)


def test_normalize_scoreboard_ranks_and_school_names():
    games = normalize_scoreboard(load("espn_scoreboard.json"))
    assert len(games) == 12 and all(g["sport"] == "ncaaf" for g in games)
    by_id = {g["id"]: g for g in games}
    live = by_id["401756003"]
    assert live["phase"] == "live" and live["period"] == "3rd" and live["clock"] == "7:12"
    assert live["away"]["abbrev"] == "MICH" and live["away"]["name"] == "Michigan" and live["away"]["rank"] == 8
    assert live["home"]["name"] == "Oklahoma" and live["home"]["rank"] == 20 and live["home"]["timeouts"] == 3
    assert live["situation"]["possession"] == "away" and live["situation"]["red_zone"] and live["situation"]["text"] == "2nd & 7"
    assert isinstance(live["away"]["color"], tuple) and live["away"]["color"] == (0, 39, 76)
    assert by_id["401756004"]["phase"] == "intermission" and by_id["401756004"]["period"] == "HALF"
    assert by_id["401756002"]["outcome"] == "FINAL/OT" and by_id["401756001"]["outcome"] == "FINAL"
    unranked = by_id["401756009"]
    assert unranked["away"]["rank"] is None and unranked["home"]["rank"] is None
    assert by_id["401756005"]["week"] == 2 and by_id["401756005"]["type"] == 2


def test_normalize_standings_by_conference_with_nested_divisions():
    st = normalize_standings(load("espn_standings.json"))
    assert len(st["teams"]) == 136 and len(st["league"]) == 136
    assert set(st["division"]) == {"ACC", "Big 12", "Big Ten", "SEC", "American", "CUSA", "MAC", "MWC", "Pac-12", "Sun Belt", "Ind"}
    assert len(st["division"]["Big Ten"]) == 18 and len(st["division"]["Sun Belt"]) == 14
    assert set(st["wildcard"]) == {"Sun Belt"} and set(st["wildcard"]["Sun Belt"]) == {"East", "West"}
    row = st["teams"]["MICH"]
    assert row["conference"] == "Big Ten" and row["division"] == "" and row["otl"] == 0
    assert row["conf_record"] == f"{row['conf_wins']}-{row['conf_losses']}" and row["gp"] == row["wins"] + row["losses"]
    assert st["teams"]["APP"]["division"] == "East" and st["teams"]["TROY"]["division"] == "West"
    # conference order: conference win pct first, then overall
    order = st["division"]["SEC"]

    def key(a):
        r = st["teams"][a]
        played = r["conf_wins"] + r["conf_losses"]
        return (-(r["conf_wins"] / played if played else 0), -r["conf_wins"], -r["wins"], r["losses"], a)
    assert order == sorted(order, key=key) and len({key(a)[0] for a in order}) > 1
    assert [st["teams"][a]["conference_rank"] for a in order] == list(range(1, 17))
    assert st["teams"][st["league"][0]]["league_rank"] == 1
    # a standings payload with the NFL's camel-case stat names still parses
    nfl_like = {"children": [{"abbreviation": "X", "standings": {"entries": [{"team": {"abbreviation": "AAA"}, "stats": [
        {"name": "wins", "value": 3}, {"name": "losses", "value": 1}, {"name": "winPercent", "displayValue": ".750"},
        {"name": "vs. Conf.", "type": "vsconf", "displayValue": "2-1"}]}]}}]}
    r = normalize_standings(nfl_like)["teams"]["AAA"]
    assert r["win_pct"] == ".750" and r["conf_record"] == "2-1"


def test_team_summary_carries_rank_and_conference_record():
    st = normalize_standings(load("espn_standings.json"))
    ts = team_summary("MICH", st, load("espn_schedule_MICH.json"), TODAY)
    rec = ts["record"]
    assert rec["rank"] == 8 and rec["conference"] == "Big Ten" and rec["division"] == "Big Ten" and rec["conf_record"]
    assert ts["prev_game"]["result"] == "W" and ts["prev_game"]["opponent"] == "NMSU" and ts["prev_game"]["score"] == 45
    assert ts["next_game"]["opponent"] == "OU" and not ts["next_game"]["home"] and ts["next_game"]["week"] == 2
    empty = team_summary("OSU", None, None, TODAY)
    assert empty["record"]["division"] == "Big Ten" and empty["record"]["rank"] is None and empty["next_game"] is None


def test_slate_filters_but_keeps_favourites():
    games = normalize_scoreboard(load("espn_scoreboard.json"))
    ranked = slate(games, NcaafConfig(favorites=[], slate="ranked"))
    assert ranked and all(g["away"]["rank"] or g["home"]["rank"] for g in ranked) and len(ranked) < len(games)
    assert not any(g["id"] == "401756009" for g in ranked)                   # TROY @ APP, nobody ranked
    keep = slate(games, NcaafConfig(favorites=["APP"], slate="ranked"))
    assert any(g["id"] == "401756009" for g in keep)
    confs = slate(games, NcaafConfig(favorites=["JMU"], slate="conferences"))
    assert {g["id"] for g in confs} == {"401756009", "401756010"}
    assert slate(games, NcaafConfig(slate="all")) == games


def test_scoring_events_use_the_ncaaf_prefix():
    store = SnapshotStore()
    g = normalize_scoreboard(load("espn_scoreboard.json"))[2]
    base = {**g, "away": {**g["away"], "score": 0}, "home": {**g["home"], "score": 0}}
    s0 = store.publish("ncaaf.main_event", base)
    s1 = store.publish("ncaaf.main_event", {**base, "away": {**base["away"], "score": 7}})
    s2 = store.publish("ncaaf.main_event", {**base, "away": {**base["away"], "score": 7}, "home": {**base["home"], "score": 3}})
    assert [e.kind for e in detect_ncaaf(s0, s1)] == ["ncaaf.touchdown"]
    fg = list(detect_ncaaf(s1, s2))
    assert [e.kind for e in fg] == ["ncaaf.field_goal"] and fg[0].team == "OU" and fg[0].payload["score"] == "7-3"
    other = SnapshotStore()
    n0 = other.publish("nfl.main_event", base)
    assert list(detect_ncaaf(n0, other.publish("nfl.main_event", {**base, "away": {**base["away"], "score": 14}}))) == []


def _ctx(snap, t, ev=None, w=128, h=64):
    now = datetime(2026, 9, 5, 13, tzinfo=ZoneInfo("America/Toronto"))
    return BoardContext(snapshot=snap, profile=profile_for(w, h), width=w, height=h, fps=30, now=now, elapsed=t, event=ev)


def _snapshot():
    games = normalize_scoreboard(load("espn_scoreboard.json"))
    st = normalize_standings(load("espn_standings.json"))
    live = {**games[2], "favorite_side": "away"}
    store = SnapshotStore()
    store.publish("ncaaf.scores", games); store.publish("ncaaf.standings", st)
    store.publish("ncaaf.team_summary", {"MICH": team_summary("MICH", st, load("espn_schedule_MICH.json"), TODAY)})
    store.publish("ncaaf.season", {"sport": "ncaaf", "phase": "regular"})
    return store.publish("main_event", live), live


def test_boards_render_at_both_sizes():
    snap, live = _snapshot()
    for board, cfg in [(NcaafGameBoard(), NcaafGameConfig()), (NcaafTickerBoard(), TickerConfig()),
                       (NcaafStandingsBoard(), NcaafStandingsConfig()), (NcaafTeamSummaryBoard(), TeamSummaryConfig())]:
        for w, h in ((128, 64), (64, 32)):
            img = board.render(_ctx(snap, 2.0, w=w, h=h), cfg)
            assert img.size == (w, h) and img.getbbox() is not None, (board.key, w, h)
    assert NcaafGameBoard().sport == "ncaaf" and NcaafGameBoard().key == "ncaaf.game"
    ranked = with_ranks(live)
    assert ranked["away"]["record"] == "#8 1-0" and ranked["home"]["record"] == "#20 1-0"
    plain = with_ranks({**live, "away": {**live["away"], "rank": None}})
    assert plain["away"]["record"] == "1-0"


def test_standings_board_pages_only_the_favourites_conferences():
    snap, _ = _snapshot()
    board = NcaafStandingsBoard()
    board.enter(_ctx(snap, 0.0), NcaafStandingsConfig())
    assert len(board._pages) == 1 and board._favorite_confs == {"Big Ten"}
    board.enter(_ctx(snap, 0.0), NcaafStandingsConfig(favorite_conferences_only=False))
    assert len(board._pages) == 11
    board.enter(_ctx(snap, 0.0), NcaafStandingsConfig(view="wildcard", favorite_conferences_only=False))
    assert len(board._pages) == 1                      # only the Sun Belt has divisions
    board.enter(_ctx(snap, 0.0), NcaafStandingsConfig(view="wildcard"))
    assert len(board._pages) == 1 and board._pages[0][1] == []     # Big Ten has none -> an empty page, not a crash
    board.enter(_ctx(snap, 0.0), NcaafStandingsConfig(view="league"))
    assert len(board._pages) == 1
    st = snap.get("ncaaf.standings")
    assert board._points(st["teams"]["MICH"]) == st["teams"]["MICH"]["conf_record"]


def test_team_summary_lines():
    board = NcaafTeamSummaryBoard()
    lines = board._record_lines({"wins": 2, "losses": 0, "rank": 8, "conference": "Big Ten", "conf_record": "1-0", "conference_rank": 3})
    assert lines == ["#8 2-0", "BIG TEN 1-0 3RD"]
    assert board._record_lines({"wins": 0, "losses": 1, "rank": None, "conference": "SEC", "conf_record": "0-0", "conference_rank": 12}) == ["0-1", "SEC 0-0 12TH"]


def test_score_board_matches_and_renders():
    snap, live = _snapshot()
    td = Event("ncaaf.touchdown", team="MICH", payload={"side": "away", "game": live, "score": "21-10", "points": 7})
    sb = NcaafScoreBoard()
    assert sb.matches(td, NcaafScoreConfig()) and not sb.matches(Event("nfl.touchdown", team="BUF", payload={}), NcaafScoreConfig())
    assert sb.render(_ctx(snap, 1.0, td), NcaafScoreConfig()).getbbox() is not None
    fg = Event("ncaaf.field_goal", team="OU", payload={"side": "home", "game": live, "score": "21-13", "points": 3})
    assert NcaafScoreBoard().render(_ctx(snap, 1.0, fg), NcaafScoreConfig()).getbbox() is not None
    assert not sb.matches(fg, NcaafScoreConfig(opponent_scores=False))


@pytest.mark.asyncio
async def test_source_publishes_every_key(caplog):
    """One pass of both loops against ESPN's college URLs: slate, main event, standings, summaries, season."""
    store = SnapshotStore()
    published: dict[str, object] = {}
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        sb = mock.get(url__regex=r".*/college-football/scoreboard.*").mock(return_value=httpx.Response(200, json=load("espn_scoreboard.json")))
        mock.get(url__regex=r".*/college-football/standings.*").mock(return_value=httpx.Response(200, json=load("espn_standings.json")))
        mock.get(url__regex=r".*/college-football/teams\?.*").mock(return_value=httpx.Response(200, json=load("espn_teams.json")))
        mock.get(url__regex=r".*/college-football/teams/130/schedule.*").mock(return_value=httpx.Response(200, json=load("espn_schedule_MICH.json")))
        cfg = NcaafConfig(favorites=["MICH"], slate="ranked")
        ctx = SourceContext(key="ncaaf", store=store, config_getter=lambda: cfg, http=http)
        ctx.timezone = "America/Toronto"
        src = NcaafSource()
        api = src._api(ctx)
        with caplog.at_level(logging.INFO):
            await _one_pass(src._scores_loop(ctx, api))
            await _one_pass(src._standings_loop(ctx, api))
        published = dict(store.get().data)
    assert sb.calls.last.request.url.params["groups"] == "80" and sb.calls.last.request.url.params["limit"] == "200"
    assert published["ncaaf.season"]["phase"] == "regular" and published["ncaaf.season"]["week"] == 2
    assert published["ncaaf.main_event"]["id"] == "401756003" and published["ncaaf.main_event"]["favorite_side"] == "away"
    assert 0 < len(published["ncaaf.scores"]) < 12 and all(g["sport"] == "ncaaf" for g in published["ncaaf.scores"])
    assert len(published["ncaaf.standings"]["teams"]) == 136
    assert list(published["ncaaf.team_summary"]) == ["MICH"] and published["ncaaf.team_summary"]["MICH"]["record"]["rank"] == 8
    assert not [r for r in caplog.records if "ESPN lists no FBS team" in r.getMessage()]


async def _one_pass(coro):
    """Drive a `while True` source loop until its first sleep."""
    import asyncio

    task = asyncio.ensure_future(coro)
    for _ in range(50):
        await asyncio.sleep(0)
        if task.done():
            task.result()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_registry_check_warns_about_stale_entries(caplog):
    src = NcaafSource()

    class Ctx:
        log = logging.getLogger("ncaaf-test")

    with caplog.at_level(logging.INFO, logger="ncaaf-test"):
        src._check_teams(Ctx(), {a: "1" for a in NCAAF_TEAMS if a != "CONN"} | {"UCONN": "41"})
        src._check_teams(Ctx(), {})                       # only reported once
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 2 and "CONN" in msgs[0] and "UCONN" in msgs[1]


def test_logo_urls_for_college_come_from_the_team_index():
    index = {"MICH": {"full/default": "https://a.espncdn.com/i/teamlogos/ncaa/500/130.png",
                      "full/dark": "https://a.espncdn.com/i/teamlogos/ncaa/500-dark/130.png"}}
    assert logos._url("ncaaf", "MICH", "default", index) == "https://a.espncdn.com/i/teamlogos/ncaa/500/130.png"
    assert logos._url("ncaaf", "MICH", "dark", index) == "https://a.espncdn.com/i/teamlogos/ncaa/500-dark/130.png"
    assert logos._url("ncaaf", "OSU", "default", index) is None          # not indexed: no guessing an id
    assert logos._url("nfl", "BUF", "default", {}) == "https://a.espncdn.com/i/teamlogos/nfl/500/buf.png"
    assert "groups=80" in logos.TEAMS_API.format(path=logos.LEAGUE_PATHS["ncaaf"], query=logos.TEAMS_QUERY["ncaaf"])


def test_wired_into_config_and_dashboard():
    cfg = AppConfig()
    assert "ncaaf" in cfg.sports.priority
    assert any(e.board == "ncaaf.game" for e in cfg.playlists.live)
    snap, _ = _snapshot()
    summary = dashboard_summary(snap, TODAY)
    block = next(s for s in summary["sports"] if s["sport"] == "ncaaf")
    assert block["title"] == "College football" and block["favorites"] == ["MICH"]
    assert any(g["main"] for d in block["days"] for g in d["games"])
    assert block["teams"]["MICH"]["record"]["rank"] == 8
