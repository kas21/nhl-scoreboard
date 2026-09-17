import json
from pathlib import Path

import httpx
import pytest
import respx

from scoreboard.boards.base import BoardContext
from scoreboard.data import Event, SnapshotStore
from scoreboard.data.source import SourceContext
from scoreboard.ncaah.boards.game import NcaahGameBoard, NcaahGameConfig, with_ranks
from scoreboard.ncaah.boards.others import (
    NcaahGoalBoard,
    NcaahGoalConfig,
    NcaahTeamSummaryBoard,
    NcaahTickerBoard,
)
from scoreboard.ncaah.events import detect_ncaah
from scoreboard.ncaah.normalize import (
    normalize_game,
    normalize_scoreboard,
    record_from_schedule,
    schedule_games,
    team_summary,
)
from scoreboard.ncaah.source import NcaahConfig, NcaahSource, slate
from scoreboard.ncaah.teams import (
    API_ABBREVS,
    COLORS,
    CONFERENCE_OF,
    CONFERENCES,
    NCAAH_TEAMS,
    REGISTRY_ABBREVS,
    colors,
)
from scoreboard.nhl.boards.team_summary import TeamSummaryConfig
from scoreboard.nhl.boards.ticker import TickerConfig
from scoreboard.render.profiles import profile_for

FIX = Path(__file__).parent / "fixtures" / "ncaah"


def load(n):
    return json.loads((FIX / n).read_text())


def test_registry_is_division_one_with_espn_codes():
    assert len(NCAAH_TEAMS) == 64 and len(CONFERENCES) == 7
    assert CONFERENCE_OF["MICH"] == "Big Ten" and CONFERENCE_OF["STMN"] == "NCHC" and CONFERENCE_OF["UAA"] == "Independents"
    assert set(COLORS) == set(NCAAH_TEAMS)                  # every school has a curated sweater colour
    assert colors("MICH")[0] == (0, 39, 76) and colors("XXX")[0] == (90, 90, 90)
    # every registry code resolves to an entry in ESPN's team API, via the alias map where the spellings differ
    teams = load("espn_teams.json")["sports"][0]["leagues"][0]["teams"]
    ids = NcaahSource()._team_ids(teams)
    assert set(NCAAH_TEAMS) <= set(ids) and ids["MICH"] == "130" and ids["AFA"] == "2005" and "AF" not in ids
    assert API_ABBREVS == {"AFA": "AF", "WISC": "WIS"} and REGISTRY_ABBREVS["WIS"] == "WISC"
    # and the scoreboard's own codes are the registry's
    codes = {s["abbrev"] for g in normalize_scoreboard(load("espn_scoreboard_2026-01-10.json")) for s in (g["away"], g["home"])}
    assert codes <= set(NCAAH_TEAMS)


def test_normalize_scoreboard_finals_and_overtime():
    games = normalize_scoreboard(load("espn_scoreboard_2026-01-10.json"), {"MICH": "16-3-1"})
    assert len(games) == 27 and all(g["sport"] == "ncaah" for g in games)
    g = next(x for x in games if x["home"]["abbrev"] == "BRWN")
    assert g["phase"] == "postgame" and g["state"] == "OFF" and g["outcome"] == "FINAL" and g["period"] == "3rd"
    assert g["away"]["abbrev"] == "USL" and g["home"]["name"] == "Brown" and g["home"]["score"] == 4 and g["away"]["score"] == 1
    assert g["home"]["record"] == "" and g["date"] == "2026-01-10"
    mich = next(x for x in games if x["home"]["abbrev"] == "MICH")
    assert mich["home"]["record"] == "16-3-1" and mich["home"]["color"] == (0, 39, 76)
    ot = [x for x in normalize_scoreboard(load("espn_scoreboard_2026-03-07.json")) if x["outcome"] != "FINAL"]
    assert ot and all(x["outcome"] == "FINAL/OT" and x["period"] == "OT" and x["period_number"] == 4 for x in ot)


def test_normalize_live_intermission_tbd_and_shots():
    payload = load("espn_scoreboard_2026-01-10.json")
    ev = next(e for e in payload["events"] if e["shortName"] == "ND @ MICH")
    comp = ev["competitions"][0]
    comp["status"] = {"period": 2, "displayClock": "12:34", "type": {"state": "in", "name": "STATUS_IN_PROGRESS", "shortDetail": "12:34 - 2nd"}}
    for c in comp["competitors"]:
        c["score"] = "2" if c["homeAway"] == "home" else "1"
        c["statistics"] = [{"name": "saves", "displayValue": "11" if c["homeAway"] == "home" else "18"}]
        c["curatedRank"] = {"current": 3 if c["homeAway"] == "home" else 99}
    g = normalize_game(ev)
    assert g["phase"] == "live" and g["state"] == "LIVE" and g["period"] == "2nd" and g["clock"] == "12:34" and g["clock_running"]
    assert g["home"]["sog"] == 18 + 2 and g["away"]["sog"] == 11 + 1        # shots = the other goalie's saves + own goals
    assert g["home"]["rank"] == 3 and g["away"]["rank"] is None
    comp["status"]["type"]["name"] = "STATUS_END_PERIOD"
    g = normalize_game(ev)
    assert g["phase"] == "intermission" and g["in_intermission"] and not g["clock_running"]
    comp["status"] = {"period": 5, "displayClock": "0:00", "type": {"state": "post", "name": "STATUS_FINAL", "shortDetail": "Final/SO"}}
    assert normalize_game(ev)["outcome"] == "FINAL/SO" and normalize_game(ev)["period"] == "SO"
    comp["status"] = {"period": 0, "displayClock": "0:00", "type": {"state": "pre", "name": "STATUS_POSTPONED", "shortDetail": "Postponed"}}
    g = normalize_game(ev)
    assert g["phase"] == "postgame" and g["outcome"] == "PPD" and g["schedule_state"] == "PPD"
    tbd = normalize_scoreboard(load("espn_scoreboard_upcoming.json"))[0]
    assert tbd["phase"] == "pregame" and tbd["time_tbd"] and tbd["date"] == "2026-10-02" and tbd["away"]["name"] == "Bowling Green"


def test_team_summary_and_record_come_from_the_schedule():
    schedule = load("espn_schedule_MICH_2025-26.json")
    games = schedule_games(schedule)
    assert record_from_schedule(games, "MICH") == {"wins": 30, "losses": 8, "ties": 2, "gp": 40}
    ts = team_summary("MICH", schedule, "2026-04-12")
    rec = ts["record"]
    assert rec["wins"] == 30 and rec["ties"] == 2 and rec["conference"] == "Big Ten" and rec["rank"] == 1 and rec["streak"] == "L1"
    assert ts["prev_game"]["opponent"] == "DEN" and ts["prev_game"]["result"] == "L" and ts["prev_game"]["score"] == 3
    assert ts["next_game"] is None
    mid = team_summary("MICH", schedule, "2026-01-15")
    assert mid["prev_game"] is not None and mid["next_game"] is None      # every game in the capture has been played
    empty = team_summary("OSU", None, "2026-01-15")
    assert empty["record"]["gp"] == 0 and empty["record"]["division"] == "Big Ten" and empty["prev_game"] is None


def test_slate_filters_but_keeps_favourites():
    games = normalize_scoreboard(load("espn_scoreboard_2026-01-10.json"))
    for g in games:
        if g["home"]["abbrev"] == "OSU":
            g["home"]["rank"] = 4
    ranked = slate(games, NcaahConfig(favorites=[], slate="ranked"))
    assert 0 < len(ranked) < len(games) and all(g["away"]["rank"] or g["home"]["rank"] for g in ranked)
    assert any(g["home"]["abbrev"] == "OSU" for g in ranked) and not any(g["home"]["abbrev"] == "BRWN" for g in ranked)
    keep = slate(games, NcaahConfig(favorites=["BRWN"], slate="ranked"))
    assert any(g["home"]["abbrev"] == "BRWN" for g in keep) and len(keep) == len(ranked) + 1
    confs = slate(games, NcaahConfig(favorites=["MICH"], slate="conferences"))
    assert confs and all(CONFERENCE_OF[g["home"]["abbrev"]] == "Big Ten" or CONFERENCE_OF[g["away"]["abbrev"]] == "Big Ten" for g in confs)
    assert slate(games, NcaahConfig(slate="all")) == games


def test_season_counts_down_to_the_opener():
    src = NcaahSource()
    upcoming = normalize_scoreboard(load("espn_scoreboard_upcoming.json"))
    season = src._season(upcoming, "2026-09-17")
    assert season["phase"] == "offseason" and season["regular_start"] == "2026-10-02" and season["days_to_regular"] == 15
    played = normalize_scoreboard(load("espn_scoreboard_2026-01-10.json"))
    assert src._season(played, "2026-01-10")["phase"] == "regular"
    assert src._season([], "2026-07-01")["phase"] == "offseason"


def test_goal_events_use_the_ncaah_prefix():
    store = SnapshotStore()
    g = normalize_scoreboard(load("espn_scoreboard_2026-01-10.json"))[0]
    base = {**g, "away": {**g["away"], "score": 0}, "home": {**g["home"], "score": 0}, "state": "LIVE"}
    s0 = store.publish("ncaah.main_event", base)
    s1 = store.publish("ncaah.main_event", {**base, "home": {**base["home"], "score": 1}})
    events = list(detect_ncaah(s0, s1))
    assert [e.kind for e in events] == ["ncaah.goal"] and events[0].team == "BRWN" and events[0].payload["score"] == "0-1"
    assert events[0].payload["goal"] is None                        # ESPN has no scorer feed


def test_boards_render_at_every_size():
    games = normalize_scoreboard(load("espn_scoreboard_2026-01-10.json"), {"MICH": "16-3-1"})
    live = {**next(g for g in games if g["home"]["abbrev"] == "MICH"), "state": "LIVE", "phase": "live", "period": "2nd", "clock": "12:34",
            "outcome": "", "favorite_side": "home"}
    live["home"] = {**live["home"], "rank": 3}
    store = SnapshotStore()
    store.publish("ncaah.scores", games)
    store.publish("ncaah.team_summary", {"MICH": team_summary("MICH", load("espn_schedule_MICH_2025-26.json"), "2026-04-12")})
    snap = store.publish("main_event", live)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime(2026, 1, 10, 21, tzinfo=ZoneInfo("America/Toronto"))
    goal = Event("ncaah.goal", team="MICH", payload={"side": "home", "game": live, "score": "1-2", "goal": None})
    for w, h in ((128, 64), (64, 32), (128, 32), (192, 128)):
        ctx = BoardContext(snapshot=snap, profile=profile_for(w, h), width=w, height=h, fps=30, now=now, elapsed=2.0, event=goal)
        for board, cfg in ((NcaahGameBoard(), NcaahGameConfig()), (NcaahTickerBoard(), TickerConfig()),
                           (NcaahTeamSummaryBoard(), TeamSummaryConfig()), (NcaahGoalBoard(), NcaahGoalConfig())):
            board.enter(ctx, cfg)
            assert board.render(ctx, cfg).size == (w, h)
    assert with_ranks(live)["home"]["record"] == "#3 16-3-1" and with_ranks(live)["away"]["record"] == live["away"]["record"]
    assert NcaahGameBoard().sport == "ncaah" and NcaahGoalBoard().matches(goal, NcaahGoalConfig())


class _StopLoop(Exception):
    pass


@pytest.mark.asyncio
async def test_record_loop_learns_records_and_merges_them_into_the_slate(monkeypatch):
    from scoreboard.ncaah import source as ncaah_source
    monkeypatch.setattr(ncaah_source, "_today", lambda ctx: "2026-04-12")

    async def stop(seconds: float, **_: object) -> None:
        raise _StopLoop

    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*/mens-college-hockey/teams\?.*").mock(return_value=httpx.Response(200, json=load("espn_teams.json")))
        mock.get(url__regex=r".*/mens-college-hockey/teams/130/schedule.*").mock(return_value=httpx.Response(200, json=load("espn_schedule_MICH_2025-26.json")))
        mock.get(url__regex=r".*/mens-college-hockey/standings.*").mock(return_value=httpx.Response(200, json=load("espn_standings.json")))
        cfg = NcaahConfig(favorites=["MICH"])
        store = SnapshotStore()
        ctx = SourceContext(key="ncaah", store=store, config_getter=lambda: cfg, http=http)
        monkeypatch.setattr(ctx, "nap", stop)                # the loop naps through ctx, so a settings save can wake it
        src = NcaahSource()
        with pytest.raises(_StopLoop):
            await src._standings_loop(ctx, src._api(ctx))
    summary = store.get().get("ncaah.team_summary")["MICH"]
    assert summary["record"]["wins"] == 30 and src._records == {"MICH": "30-8-2"}
    assert "ncaah.standings" not in store.get().data                      # no standings feed, no standings key
    games = src._scoreboard(load("espn_scoreboard_2026-01-10.json"), None)
    assert next(g for g in games if g["home"]["abbrev"] == "MICH")["home"]["record"] == "30-8-2"
