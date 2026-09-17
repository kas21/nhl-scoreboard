import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from scoreboard import logos
from scoreboard.ahl.api import AhlApi, _decode
from scoreboard.ahl.boards.game import AhlGameBoard, AhlGameConfig
from scoreboard.ahl.boards.others import (
    AhlGoalBoard,
    AhlGoalConfig,
    AhlPenaltyBoard,
    AhlPenaltyConfig,
    AhlStandingsBoard,
    AhlStandingsConfig,
    AhlTeamSummaryBoard,
    AhlTickerBoard,
)
from scoreboard.ahl.events import detect_ahl
from scoreboard.ahl.normalize import (
    elapsed_seconds,
    enrich_from_summary,
    normalize_game,
    normalize_scorebar,
    normalize_standings,
    pick_seasons,
    season_types,
    situation_from_summary,
    team_summary,
)
from scoreboard.ahl.source import AhlConfig, AhlSource
from scoreboard.ahl.teams import AHL_TEAMS, CLUBS, DIVISIONS, colors, full_name
from scoreboard.boards.base import BoardContext
from scoreboard.data import Event, SnapshotStore
from scoreboard.data.source import SourceContext
from scoreboard.nhl.boards.team_summary import TeamSummaryConfig
from scoreboard.nhl.boards.ticker import TickerConfig
from scoreboard.render.profiles import profile_for

FIX = Path(__file__).parent / "fixtures" / "ahl"
LIVE_ROW = {"ID": "1027771", "SeasonID": "88", "game_letter": "V", "Date": "2025-06-21", "GameDateISO8601": "2025-06-21T18:00:00-07:00",
            "HomeID": "440", "HomeCode": "ABB", "HomeNickname": "Canucks", "HomeCity": "Abbotsford", "HomeGoals": "1",
            "VisitorID": "384", "VisitorCode": "CLT", "VisitorNickname": "Checkers", "VisitorCity": "Charlotte", "VisitorGoals": "1",
            "HomeWins": "3", "HomeRegulationLosses": "1", "HomeOTLosses": "0", "HomeShootoutLosses": "0",
            "VisitorWins": "1", "VisitorRegulationLosses": "3", "VisitorOTLosses": "0", "VisitorShootoutLosses": "0",
            "Period": "1", "PeriodNameShort": "1", "GameClock": "05:30", "GameStatus": "2", "Intermission": "0", "GameStatusString": "1st 05:30"}


def load(n):
    return json.loads((FIX / n).read_text())


def sitekit(n):
    return load(n)["SiteKit"]


def summary():
    return load("gamesummary_1027771.json")["GC"]["Gamesummary"]


def test_registry_matches_the_league_team_list():
    listed = {t["code"] for t in sitekit("teamsbyseason.json")["Teamsbyseason"]}
    assert set(AHL_TEAMS) == listed and len(AHL_TEAMS) == 32
    assert set(DIVISIONS) == {"Atlantic", "North", "Central", "Pacific"} and sum(len(v) for v in DIVISIONS.values()) == 32
    assert {c[3] for c in CLUBS.values()} == {t for t in __import__("scoreboard.nhl.teams", fromlist=["NHL_TEAMS"]).NHL_TEAMS}   # one affiliate per NHL club
    assert full_name("WBS") == "Wilkes-Barre/Scranton Penguins"
    assert colors("TOR")[0] == (0, 32, 91) and colors("HER")[0] == (78, 42, 30) and colors("BRI")[0] == (90, 90, 90)


def test_scorebar_normalises_finals_and_scheduled_games():
    types = season_types(sitekit("seasons.json"))
    assert types["94"] == 2 and types["93"] == 1 and types["92"] == 3
    games = normalize_scorebar(sitekit("scorebar.json"), types)
    assert len(games) == 13 and all(g["sport"] == "ahl" for g in games)
    by = {(g["away"]["abbrev"], g["home"]["abbrev"]): g for g in games}
    so = by[("HSK", "TUC")]
    assert so["phase"] == "postgame" and so["outcome"] == "FINAL/SO" and so["period"] == "SO" and so["away"]["record"] == "39-21-12"
    assert by[("ROC", "HER")]["outcome"] == "FINAL/OT" and by[("CV", "ONT")]["outcome"] == "FINAL/2OT" and by[("CV", "ONT")]["type"] == 3
    assert by[("MIL", "CHI")]["period"] == "3rd" and by[("MIL", "CHI")]["clock"] == "" and by[("MIL", "CHI")]["type"] == 2
    pre = by[("MB", "IA")]
    assert pre["phase"] == "pregame" and pre["state"] == "FUT" and pre["type"] == 1 and pre["period"] == "" and pre["date"] == "2026-09-25"
    assert pre["start_time_utc"] == "2026-09-25T19:00:00Z" and pre["away"]["name"] == "Moose" and pre["venue"] == "TRIA Rink"
    assert by[("HAM", "BEL")]["type"] == 2
    assert games == sorted(games, key=lambda g: (g["date"], g["start_time_utc"]))


def test_scorebar_live_intermission_and_not_played():
    g = normalize_game(LIVE_ROW)
    assert g["state"] == "LIVE" and g["phase"] == "live" and g["period"] == "1st" and g["clock"] == "05:30" and g["clock_running"]
    assert g["type"] == 3 and g["home"]["record"] == "3-1-0"
    inter = normalize_game({**LIVE_ROW, "Intermission": "1", "GameClock": "00:00"})
    assert inter["phase"] == "intermission" and inter["in_intermission"] and not inter["clock_running"]
    ppd = normalize_game({**LIVE_ROW, "GameStatus": "1", "GameStatusString": "Postponed"})
    assert ppd["phase"] == "postgame" and ppd["outcome"] == "PPD" and ppd["schedule_state"] == "PPD"
    unknown = normalize_game({**LIVE_ROW, "GameStatus": "9", "GameStatusString": "Final", "PeriodNameShort": "OT"})
    assert unknown["state"] == "OFF" and unknown["outcome"] == "FINAL/OT"


def test_summary_adds_goals_penalties_shots_and_the_power_play():
    e = enrich_from_summary(normalize_game(LIVE_ROW), summary())
    assert e["away"]["sog"] == 40 and e["home"]["sog"] == 32
    assert len(e["goals"]) == 7 and len(e["penalties"]) == 6
    first, second = e["goals"][0], e["goals"][1]
    assert first["team"] == "CLT" and first["scorer"] == "Ben Steeves" and first["sweater"] == "6" and first["assists"] == ["Jack Devine", "Oliver Okuliar"]
    assert first["away_score"] == 1 and first["home_score"] == 0 and first["period"] == 1 and first["time"] == "12:55"
    assert second["team"] == "ABB" and second["strength"] == "pp" and second["home_score"] == 1 and e["goals"][-1]["away_score"] == 4
    assert [g["assists"] for g in e["goals"] if g["scorer"] == "Brett Chorske"] == [["Jesse Puljujärvi"]]      # a missing second assist is dropped
    pen = e["penalties"][0]
    assert pen["team"] == "CLT" and pen["duration"] == 2 and pen["desc"] == "Tripping" and pen["player"] == "Jesse Puljujärvi" and pen["type"] == "MINOR"
    # 1st period, 05:30 left = 870 s in; Charlotte minors were called at 806 s and 882 s, Abbotsford scored on the power play at 904 s
    assert e["powerplay"] == {"code": "h54", "clock": "00:56"}
    two = enrich_from_summary(normalize_game({**LIVE_ROW, "GameClock": "05:00"}), summary())      # 900 s: both minors running
    assert two["powerplay"]["code"] == "h53"
    over = enrich_from_summary(normalize_game({**LIVE_ROW, "GameClock": "04:00"}), summary())     # 960 s: the goal ended the first
    assert over["powerplay"] == {"code": "h54", "clock": "00:42"}
    done = enrich_from_summary(normalize_game({**LIVE_ROW, "GameClock": "02:00"}), summary())
    assert done["powerplay"]["code"] == "ev" and done["powerplay"]["clock"] == ""
    final = enrich_from_summary(normalize_game({**LIVE_ROW, "GameStatus": "4", "GameStatusString": "Final"}), summary())
    assert final["powerplay"]["code"] == "ev" and final["goals"] and final["away"]["sog"] == 40
    assert elapsed_seconds(summary(), 4, "04:38", 3) == 3 * 1200 + (1200 - 278)


def test_power_play_goal_ends_a_minor_early():
    s = {"periods": {"1": {"length": "1200"}}, "goals": [{"s": 100, "team_id": "2", "power_play": "1"}],
         "penalties": [{"s": 60, "team_id": "1", "minutes": 2, "penalty_class": "Minor", "period_id": "1", "time_off_formatted": "1:00"}]}
    g = {"phase": "live", "period_number": 1, "clock": "18:40", "type": 2, "away": {"abbrev": "A"}, "home": {"abbrev": "H"}}   # 80 s in
    sides = {"1": "A", "2": "H"}
    assert situation_from_summary(s, g, sides) == ("h54", "01:40")                          # the goal at 100 s has not happened yet
    assert situation_from_summary(s, {**g, "clock": "18:10"}, sides) == ("ev", "")        # 110 s: the goal at 100 s killed it
    second = {**s, "penalties": [{**s["penalties"][0], "period_id": "2", "s": 60}], "goals": []}
    assert situation_from_summary(second, {**g, "period_number": 2, "clock": "18:40"}, sides) == ("h54", "01:40")   # ``s`` counts within the period
    coincidental = {**s, "goals": [], "penalties": [*s["penalties"], {**s["penalties"][0], "team_id": "2"}]}
    assert situation_from_summary(coincidental, g, sides) == ("ev", "")


def test_standings_from_the_statview_feed():
    st = normalize_standings(_decode((FIX / "standings_2025-26.json").read_text()))
    assert len(st["teams"]) == 32 and set(st["division"]) == {"Atlantic", "North", "Central", "Pacific"}
    pro = st["teams"]["PRO"]
    assert pro == {"abbrev": "PRO", "conference": "Eastern", "division": "Atlantic", "gp": 72, "wins": 54, "losses": 16, "otl": 2,
                   "points": 110, "win_pct": "0.764", "l10": [6, 3, 1], "streak": "L2", "division_rank": 1, "conference_rank": 1,
                   "league_rank": 1, "wildcard_rank": 0, "clinch": "y"}
    assert st["division"]["Atlantic"][0] == "PRO" and st["league"][0] == "PRO" and len(st["division"]["Pacific"]) == 10
    assert set(st["wildcard"]) == {"Eastern", "Western"} and set(st["wildcard"]["Western"]) == {"Central", "Pacific"}
    ranks = [st["teams"][a]["conference_rank"] for a in st["wildcard"]["Eastern"]["Atlantic"] + st["wildcard"]["Eastern"]["North"]]
    assert sorted(ranks) == list(range(1, 16))
    assert normalize_standings([]) == {"teams": {}, "division": {}, "wildcard": {}, "league": []}


def test_team_summary_from_the_club_schedule():
    st = normalize_standings(_decode((FIX / "standings_2025-26.json").read_text()))
    ts = team_summary("ABB", st, sitekit("schedule_ABB.json"), "2026-09-17")
    assert ts["record"]["wins"] == 28 and ts["record"]["points"] == 63 and ts["record"]["division"] == "Pacific" and ts["record"]["streak"] == "W4"
    assert ts["prev_game"]["opponent"] == "HSK" and ts["prev_game"]["result"] == "L" and ts["prev_game"]["home"]
    assert ts["next_game"]["opponent"] == "CGY" and not ts["next_game"]["home"] and ts["next_game"]["date"] == "2026-10-02"
    assert ts["next_game"]["start_time_utc"] == "2026-10-03T01:00:00Z"
    bare = team_summary("HAM", None, None, "2026-09-17")
    assert bare["record"]["division"] == "North" and bare["prev_game"] is None


def test_seasons_pick_standings_schedule_and_phase():
    seasons = sitekit("seasons.json")
    off = pick_seasons(seasons, date(2026, 9, 17))
    assert off["phase"] == "offseason" and off["standings_season_id"] == 90 and off["schedule_season_id"] == 94
    assert off["info"]["days_to_preseason"] == 7 and off["info"]["days_to_regular"] == 15 and off["info"]["standings_final"]
    assert off["info"]["season_id"] == 20262027 and off["info"]["standings_season_id"] == 20252026
    assert pick_seasons(seasons, date(2026, 1, 15))["phase"] == "regular" and pick_seasons(seasons, date(2026, 1, 15))["schedule_season_id"] == 90
    assert pick_seasons(seasons, date(2026, 5, 1))["phase"] == "playoffs"
    assert pick_seasons(seasons, date(2026, 9, 26))["phase"] == "preseason"
    reg = pick_seasons(seasons, date(2026, 10, 5))
    assert reg["phase"] == "regular" and reg["standings_season_id"] == 94 and not reg["info"]["standings_final"]


def test_goal_and_penalty_events_use_the_ahl_prefix():
    store = SnapshotStore()
    live = enrich_from_summary(normalize_game(LIVE_ROW), summary())
    before = {**live, "home": {**live["home"], "score": 0}, "goals": live["goals"][:1], "penalties": live["penalties"][:1]}
    s0 = store.publish("ahl.main_event", before)
    s1 = store.publish("ahl.main_event", {**live, "goals": live["goals"][:2], "penalties": live["penalties"][:2]})
    kinds = {e.kind: e for e in detect_ahl(s0, s1)}
    assert set(kinds) == {"ahl.goal", "ahl.penalty"}
    assert kinds["ahl.goal"].team == "ABB" and kinds["ahl.goal"].payload["goal"]["scorer"] == "Linus Karlsson"
    assert kinds["ahl.penalty"].payload["penalty"]["desc"] == "Roughing"


def test_boards_render_at_every_size():
    types = season_types(sitekit("seasons.json"))
    games = normalize_scorebar(sitekit("scorebar.json"), types)
    st = normalize_standings(_decode((FIX / "standings_2025-26.json").read_text()))
    live = {**enrich_from_summary(normalize_game(LIVE_ROW, types), summary()), "favorite_side": "home"}
    store = SnapshotStore()
    store.publish("ahl.scores", games)
    store.publish("ahl.standings", st)
    store.publish("ahl.team_summary", {"ABB": team_summary("ABB", st, sitekit("schedule_ABB.json"), "2026-09-17")})
    snap = store.publish("main_event", live)
    now = datetime(2026, 4, 19, 16, tzinfo=ZoneInfo("America/Toronto"))
    goal = Event("ahl.goal", team="ABB", payload={"side": "home", "game": live, "score": "1-1", "goal": live["goals"][1]})
    pen = Event("ahl.penalty", team="CLT", payload={"penalty": live["penalties"][0], "game": live})
    for w, h in ((128, 64), (64, 32), (128, 32), (192, 128)):
        for board, cfg, ev in ((AhlGameBoard(), AhlGameConfig(), None), (AhlTickerBoard(), TickerConfig(), None),
                               (AhlStandingsBoard(), AhlStandingsConfig(), None), (AhlStandingsBoard(), AhlStandingsConfig(view="wildcard"), None),
                               (AhlStandingsBoard(), AhlStandingsConfig(view="league"), None),
                               (AhlTeamSummaryBoard(), TeamSummaryConfig(), None), (AhlGoalBoard(), AhlGoalConfig(), goal),
                               (AhlPenaltyBoard(), AhlPenaltyConfig(), pen)):
            ctx = BoardContext(snapshot=snap, profile=profile_for(w, h), width=w, height=h, fps=30, now=now, elapsed=2.0, event=ev)
            board.enter(ctx, cfg)
            assert board.render(ctx, cfg).size == (w, h)
    pages = AhlStandingsBoard()._grouped(st, AhlStandingsConfig(view="wildcard"))
    assert [t for page in pages for t, _, _ in page] == ["EASTERN ATLANTIC", "EASTERN NORTH", "WESTERN CENTRAL", "WESTERN PACIFIC"]
    assert AhlGameBoard().sport == "ahl"


class _StopLoop(Exception):
    pass


def _mock_feed(mock):
    def route(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        view, tab, feed = q.get("view"), q.get("tab"), q.get("feed")
        if feed == "statviewfeed":
            return httpx.Response(200, text=(FIX / "standings_2025-26.json").read_text())
        if tab == "gamesummary":
            return httpx.Response(200, json=load("gamesummary_1027771.json"))
        files = {"scorebar": "scorebar.json", "seasons": "seasons.json", "teamsbyseason": "teamsbyseason.json", "schedule": "schedule_ABB.json"}
        return httpx.Response(200, json=load(files[view]))
    mock.get(url__regex=r"https://lscluster\.hockeytech\.com/.*").mock(side_effect=route)


@pytest.mark.asyncio
async def test_standings_loop_publishes_standings_summaries_season_and_logo_urls(monkeypatch):
    from scoreboard.ahl import source as ahl_source
    monkeypatch.setattr(ahl_source, "_today", lambda ctx: "2026-09-17")

    async def stop(seconds: float, **_: object) -> None:
        raise _StopLoop

    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        _mock_feed(mock)
        cfg = AhlConfig(favorites=["ABB"])
        store = SnapshotStore()
        ctx = SourceContext(key="ahl", store=store, config_getter=lambda: cfg, http=http)
        src = AhlSource()
        monkeypatch.setattr(ahl_source.asyncio, "sleep", stop)
        with pytest.raises(_StopLoop):
            await src._standings_loop(ctx, src._api(ctx))
        if src._logo_task:
            src._logo_task.cancel()
    snap = store.get()
    assert snap.get("ahl.standings")["teams"]["PRO"]["points"] == 110
    assert snap.get("ahl.team_summary")["ABB"]["next_game"]["opponent"] == "CGY"
    season = snap.get("ahl.season")
    assert season["phase"] == "offseason" and season["favorite"] == "ABB" and season["first_game"]["opponent"] == "CGY"
    assert src._team_ids["ABB"] == "440" and src._season_types["93"] == 1
    assert logos._direct["ahl"]["ABB"] == "https://assets.leaguestat.com/ahl/logos/440.png"


@pytest.mark.asyncio
async def test_scores_loop_follows_the_favourite_and_enriches_a_live_game(monkeypatch):
    from scoreboard.ahl import source as ahl_source
    monkeypatch.setattr(ahl_source, "_today", lambda ctx: "2026-04-19")
    slept: list[float] = []

    async def stop(seconds: float, **_: object) -> None:
        slept.append(seconds)
        raise _StopLoop

    payload = load("scorebar.json")
    live_row = {**next(r for r in payload["SiteKit"]["Scorebar"] if r["HomeCode"] == "HER"), "GameStatus": "2", "Period": "2",
                "PeriodNameShort": "2", "GameClock": "10:00", "GameStatusString": "2nd 10:00"}
    payload["SiteKit"]["Scorebar"] = [r for r in payload["SiteKit"]["Scorebar"] if r["HomeCode"] != "HER"] + [live_row]
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*view=scorebar.*").mock(return_value=httpx.Response(200, json=payload))      # before the catch-all
        _mock_feed(mock)
        cfg = AhlConfig(favorites=["HER"], live_interval=10, idle_interval=120, show_games_within_days=2)
        store = SnapshotStore()
        ctx = SourceContext(key="ahl", store=store, config_getter=lambda: cfg, http=http)
        ctx.sleep = stop
        src = AhlSource()
        src._standings_ready.set()
        with pytest.raises(_StopLoop):
            await src._scores_loop(ctx, src._api(ctx))
    snap = store.get()
    main = snap.get("ahl.main_event")
    assert main["home"]["abbrev"] == "HER" and main["phase"] == "live" and main["favorite_side"] == "home" and main["sport"] == "ahl"
    assert main["goals"] and main["away"]["sog"] == 40                       # the summary was fetched for the live game
    assert slept == [10]
    scores = snap.get("ahl.scores")
    assert scores and all(g["date"] == "2026-04-19" for g in scores) and len(snap.get("ahl.schedule")) == len(scores)


def test_slate_keeps_results_when_the_next_game_is_far_off():
    types = season_types(sitekit("seasons.json"))
    games = normalize_scorebar(sitekit("scorebar.json"), types)
    cfg = AhlConfig(show_games_within_days=2)
    assert AhlSource._slate(games, "2026-09-17", cfg, carry=False) == []
    tonight = AhlSource._slate(games, "2026-09-25", cfg, carry=False)
    assert [g["away"]["abbrev"] for g in tonight] == ["MB", "PRO"]
    morning_after = AhlSource._slate(games, "2026-04-20", cfg, carry=True)
    assert {g["home"]["abbrev"] for g in morning_after} == {"HER", "CHI", "CV", "TOR"}      # last night's finals until the rollover hour
    assert AhlSource._slate(games, "2026-04-20", cfg, carry=False) == []


@pytest.mark.asyncio
async def test_api_unwraps_the_statview_parentheses_and_reports_failures():
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(url__regex=r".*feed=statviewfeed.*").mock(return_value=httpx.Response(200, text="([{\"sections\": []}])"))
        rows = await AhlApi(http).standings(90)
        assert rows == [{"sections": []}]
    from scoreboard.ahl.api import AhlApiError
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(url__regex=r".*").mock(return_value=httpx.Response(500))
        import scoreboard.ahl.api as api_mod
        api_mod.RETRY_DELAYS = ()
        with pytest.raises(AhlApiError):
            await AhlApi(http).seasons()
