import asyncio
import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from scoreboard import logos
from scoreboard.boards.base import BoardContext
from scoreboard.boards.season_countdown import CountdownConfig, SeasonCountdownBoard, milestone
from scoreboard.data import Event, SnapshotStore
from scoreboard.data.arbiter import choose
from scoreboard.data.source import SourceContext
from scoreboard.mlb import teams as mlb_teams
from scoreboard.mlb.api import BASE_URL, FEED_URL
from scoreboard.mlb.boards.game import MlbGameBoard, MlbGameConfig, bases_image
from scoreboard.mlb.boards.others import (
    MlbScoreBoard,
    MlbStandingsBoard,
    MlbTeamSummaryBoard,
    MlbTickerBoard,
    ScoreConfig,
)
from scoreboard.mlb.events import detect_mlb
from scoreboard.mlb.normalize import (
    enrich_from_feed,
    game_state,
    last_name,
    normalize_schedule,
    normalize_standings,
    season_info,
    team_summary,
)
from scoreboard.mlb.source import MlbConfig, MlbSource, _slate
from scoreboard.nhl.boards.standings import StandingsConfig
from scoreboard.nhl.boards.team_summary import TeamSummaryConfig
from scoreboard.nhl.boards.ticker import TickerConfig
from scoreboard.nhl.select import select_main_event
from scoreboard.render.profiles import profile_for

FIX = Path(__file__).parent / "fixtures" / "mlb"
TODAY = "2026-09-03"


def load(n):
    return json.loads((FIX / n).read_text())


def by_id(games, pk):
    return next(g for g in games if g["id"] == str(pk))


# -- registry ---------------------------------------------------------------------


def test_team_registry_is_complete_and_keyed_by_id():
    assert len(mlb_teams.MLB_TEAMS) == 30 and len(mlb_teams.DIVISIONS) == 6
    assert all(len(v) == 5 for v in mlb_teams.DIVISIONS.values())
    assert mlb_teams.abbrev_for(133) == "ATH" and mlb_teams.abbrev_for(None, "OAK") == "ATH" and mlb_teams.canonical("ARI") == "AZ"
    t = mlb_teams.team("NYY")
    assert t.full_name == "New York Yankees" and t.division == "AL East" and t.primary == (12, 35, 64)
    assert mlb_teams.team("XXX").primary == mlb_teams.DEFAULT_COLORS[0]       # unknown never raises
    assert all(mlb_teams.team(a).primary != mlb_teams.DEFAULT_COLORS[0] or a == "CWS" for a in mlb_teams.MLB_TEAMS)


def test_logo_codes_follow_espn_quirks(tmp_path):
    assert logos.espn_code("mlb", "CWS") == "chw" and logos.espn_code("mlb", "AZ") == "ari" and logos.espn_code("mlb", "NYY") == "nyy"
    assert logos.api_abbrev("mlb", "CWS") == "CHW" and logos.LEAGUE_PATHS["mlb"] == "baseball/mlb"
    img = mlb_teams.logo("NYY", 24)
    assert img.size == (24, 24) and img.getbbox() is not None                 # placeholder until fetched


# -- normalising ------------------------------------------------------------------


def test_schedule_normalises_every_status():
    games = normalize_schedule(load("schedule_2026-09-03.json"))
    assert len(games) == 7 and all(g["sport"] == "mlb" and g["date"] == TODAY for g in games)
    final = by_id(games, 776001)
    assert final["phase"] == "postgame" and final["state"] == "FINAL" and final["outcome"] == "FINAL"
    assert final["away"]["abbrev"] == "NYY" and final["away"]["score"] == 5 and final["away"]["hits"] == 11 and final["home"]["errors"] == 1
    assert final["away"]["record"] == "82-56" and final["away"]["name"] == "Yankees" and final["away"]["color"] == (12, 35, 64)
    assert final["decisions"] == {"winner": "Max Fried", "loser": "Tanner Houck", "save": "Devin Williams"}
    assert by_id(games, 776004)["outcome"] == "FINAL/11"
    live = by_id(games, 776002)
    sit = live["situation"]
    assert live["phase"] == "live" and live["state"] == "LIVE" and live["period"] == "TOP" and live["clock"] == "7TH"
    assert sit["inning"] == 7 and sit["half"] == "top" and sit["batting"] == "away"
    assert (sit["balls"], sit["strikes"], sit["outs"]) == (1, 2, 1) and sit["runners"] == [True, False, True]
    assert sit["batter"] == "Shohei Ohtani" and sit["pitcher"] == "Yu Darvish" and sit["on_deck"] == "Mookie Betts"
    assert live["clock_running"] and not live["in_intermission"]
    brk = by_id(games, 776006)
    assert brk["phase"] == "live" and brk["situation"]["half"] == "middle" and brk["period"] == "MID" and brk["situation"]["batting"] == "home"
    assert not brk["clock_running"] and brk["situation"]["batter"] == "Jose Altuve"
    pre = by_id(games, 776003)
    assert pre["phase"] == "pregame" and pre["state"] == "FUT" and pre["away"]["probable_pitcher"] == "Kevin Gausman"
    assert pre["period"] == "" and pre["clock"] == "" and pre["situation"]["batting"] is None
    assert by_id(games, 776007)["state"] == "PRE" and by_id(games, 776007)["phase"] == "pregame"       # warmup is not "live"
    ppd = by_id(games, 776005)
    assert ppd["phase"] == "postgame" and ppd["state"] == "PPD" and ppd["outcome"] == "PPD"


def test_game_state_table():
    assert game_state({"abstractGameState": "Live", "detailedState": "Delayed: Rain"}) == ("LIVE", "live", "")
    assert game_state({"abstractGameState": "Live", "detailedState": "Manager challenge: Tag play"})[0] == "LIVE"
    assert game_state({"abstractGameState": "Final", "detailedState": "Completed Early: Rain"}) == ("FINAL", "postgame", "FINAL")
    assert game_state({"abstractGameState": "Final", "detailedState": "Cancelled: Wet Grounds"})[0] == "CANCELLED"
    assert game_state({"abstractGameState": "Live", "detailedState": "Suspended: Rain"})[1] == "postgame"
    assert game_state({"abstractGameState": "Preview", "detailedState": "Delayed Start: Rain"}) == ("PRE", "pregame", "")
    assert game_state({"abstractGameState": "Preview", "detailedState": "Pre-Game"})[0] == "PRE"
    assert last_name("Fernando Tatis Jr.") == "Tatis" and last_name("Shohei Ohtani") == "Ohtani" and last_name("") == ""


def test_live_feed_enrichment():
    live = by_id(normalize_schedule(load("schedule_2026-09-03.json")), 776002)
    g = enrich_from_feed(live, load("feed_live_776002.json"))
    sit = g["situation"]
    assert sit["last_play"]["type"] == "home_run" and sit["last_play"]["label"] == "HOME RUN" and sit["last_play"]["complete"]
    assert sit["last_play"]["batting"] == "away" and sit["last_play"]["rbi"] == 2
    assert sit["pitch"] == {"speed": 96, "code": "FF", "label": "4-SEAM", "call": "In play, run(s)"}
    assert sit["pitch_count"] == 88 and sit["no_hitter"] is False
    assert live["situation"]["last_play"] is None                       # pure: the input is untouched
    final = by_id(normalize_schedule(load("schedule_2026-09-03.json")), 776001)
    g = enrich_from_feed(final, load("feed_live_776001.json"))
    assert g["decisions"] == {"winner": "Max Fried (14-4)", "loser": "Tanner Houck (8-9)", "save": "Devin Williams (31)"}
    assert enrich_from_feed(live, {})["situation"]["last_play"] is None      # an empty feed is harmless


def test_standings_and_team_summary():
    st = normalize_standings(load("standings_2026.json"))
    assert len(st["teams"]) == 30 and list(st["division"]) == list(mlb_teams.DIVISION_ORDER)
    assert all(len(v) == 5 for v in st["division"].values())
    nyy = st["teams"]["NYY"]
    assert nyy["wins"] == 82 and nyy["losses"] == 56 and nyy["games_back"] == "-" and nyy["division_rank"] == 1
    assert nyy["streak"] == "W3" and nyy["l10"] == [7, 3] and nyy["win_pct"] == ".594" and nyy["division"] == "AL East"
    assert st["division"]["AL East"][0] == "NYY" and st["teams"]["TOR"]["games_back"] == "4.0"
    assert st["teams"]["COL"]["eliminated"] and not nyy["eliminated"]
    al = st["wildcard"]["AL"]
    assert len(al["Leaders"]) == 3 and all(st["teams"][t]["division_rank"] == 1 for t in al["Leaders"])
    assert len(al["Wildcard"]) == 12 and [st["teams"][t]["wildcard_rank"] for t in al["Wildcard"]] == list(range(1, 13))
    assert st["league"][0] == "MIL" and len(st["league"]) == 30
    ts = team_summary("NYY", st, load("schedule_NYY_2026-08-24_2026-09-17.json"), TODAY)
    assert ts["record"]["wins"] == 82 and ts["record"]["division_rank"] == 1 and ts["record"]["l10"] == [7, 3]
    assert ts["prev_game"] == {**ts["prev_game"], "opponent": "BOS", "result": "W", "score": 5, "opponent_score": 3, "home": False, "date": TODAY}
    assert ts["next_game"]["opponent"] == "TOR" and ts["next_game"]["home"] and ts["next_game"]["date"] == "2026-09-05"
    assert ts["next_game"]["probable_pitcher"] == "Gerrit Cole"


def test_season_phases_and_opener():
    payload = load("seasons_2026.json")
    assert season_info(payload, date(2026, 9, 3))["phase"] == "regular"
    assert season_info(payload, date(2026, 10, 10))["phase"] == "playoffs"
    assert season_info(payload, date(2026, 9, 28))["phase"] == "playoffs"          # the gap before the wild card round
    assert season_info(payload, date(2026, 3, 1))["phase"] == "preseason"
    off = season_info(payload, date(2026, 1, 10), load("schedule_NYY_opener.json"), "NYY", standings_season=2025)
    assert off["phase"] == "offseason" and off["days_to_preseason"] == 41 and off["days_to_regular"] == 75
    assert off["first_game"] == {"date": "2026-03-26", "home": False, "opponent": "TOR", "start_time_utc": "2026-03-26T23:05:00Z"}
    assert off["standings_final"] and off["standings_season_id"] == 2025
    m = milestone({**off, "sport": "mlb"}, date(2026, 1, 10))
    assert m["days"] == 75 and m["label"] == "OPENER AT TOR"
    m = milestone({**off, "first_game": None, "sport": "mlb"}, date(2026, 1, 10))
    assert m["label"] == "SPRING TRAINING" and m["days"] == 41


# -- selection --------------------------------------------------------------------


def test_schedule_window_is_today_only_unless_asked():
    from scoreboard.mlb.source import _schedule_window
    todays = normalize_schedule(load("schedule_2026-09-03.json"))
    tomorrow = [{**g, "id": f"{g['id']}x", "date": "2026-09-04", "start_time_utc": "2026-09-04T23:05:00Z"} for g in todays[:2]]
    games = [*tomorrow, *todays]
    today_only = _schedule_window(games, TODAY, MlbConfig())
    assert today_only and all(g["date"] == TODAY for g in today_only)
    whole = _schedule_window(games, TODAY, MlbConfig(schedule_today_only=False))
    assert len(whole) == len(games) and [g["date"] for g in whole] == sorted(g["date"] for g in whole)
    assert _schedule_window(games, "2030-01-01", MlbConfig()) == []                # off day: scores carries the next slate


def test_slate_and_main_event_selection():
    games = normalize_schedule(load("schedule_2026-09-03.json"))
    assert _slate(games, TODAY) == games
    tomorrow = [{**g, "date": "2026-09-04"} for g in games[:2]] + [{**g, "date": "2026-09-05"} for g in games[2:]]
    assert {g["date"] for g in _slate(tomorrow, TODAY)} == {"2026-09-04"}
    assert _slate(games, "2026-09-09") == []
    main = select_main_event(games, ["LAD", "NYY"], today=TODAY)
    assert main["id"] == "776002"                                       # the live favourite wins
    assert select_main_event(games, ["NYY", "LAD"], today=TODAY)["id"] == "776002"   # live beats a favourite's final
    assert select_main_event(games, ["TOR"], today=TODAY)["state"] == "FUT"
    assert select_main_event(games, ["MIA"], today=TODAY)["state"] == "PPD"
    assert select_main_event(games, ["SF"], today=TODAY) is None
    assert choose({"nhl": {"sport": "nhl", "phase": "pregame"}, "mlb": {"sport": "mlb", "phase": "live"}}, ["nhl", "nfl", "mlb"])["sport"] == "mlb"


# -- events -----------------------------------------------------------------------


def test_run_and_home_run_events():
    games = normalize_schedule(load("schedule_2026-09-03.json"))
    live = enrich_from_feed(by_id(games, 776002), load("feed_live_776002.json"))
    store = SnapshotStore()
    before = {**live, "away": {**live["away"], "score": 2}, "situation": {**live["situation"], "last_play": None, "half": "top"}}
    s0 = store.publish("mlb.main_event", before)
    s1 = store.publish("mlb.main_event", live)
    kinds = [e.kind for e in detect_mlb(s0, s1)]
    assert kinds == ["mlb.home_run"]
    ev = next(e for e in detect_mlb(s0, s1) if e.kind == "mlb.home_run")
    assert ev.team == "LAD" and ev.payload["runs"] == 2 and ev.payload["score"] == "4-2" and "Ohtani" in ev.payload["text"]
    plain = {**live, "home": {**live["home"], "score": 3}, "situation": {**live["situation"], "last_play": {"type": "single", "complete": True, "batting": "home"}, "half": "bottom"}}
    s2 = store.publish("mlb.main_event", plain)
    kinds = [e.kind for e in detect_mlb(s1, s2)]
    assert "mlb.run" in kinds and "mlb.inning_change" in kinds and "mlb.home_run" not in kinds
    s3 = store.publish("mlb.main_event", {**plain, "state": "FINAL", "phase": "postgame"})
    assert [e.kind for e in detect_mlb(s2, s3)] == ["mlb.state_change"]
    assert list(detect_mlb(s3, store.publish("mlb.main_event", {**plain, "id": "1"}))) == []


# -- boards -----------------------------------------------------------------------


def _snapshot(main):
    games = normalize_schedule(load("schedule_2026-09-03.json"))
    st = normalize_standings(load("standings_2026.json"))
    store = SnapshotStore()
    store.publish("mlb.scores", games)
    store.publish("mlb.standings", st)
    store.publish("mlb.season", {"sport": "mlb", "phase": "regular", "standings_final": False})
    store.publish("mlb.team_summary", {"NYY": team_summary("NYY", st, load("schedule_NYY_2026-08-24_2026-09-17.json"), TODAY)})
    return store.publish("main_event", main)


def _ctx(snap, t, ev=None, w=128, h=64):
    now = datetime(2026, 9, 3, 19, tzinfo=ZoneInfo("America/New_York"))
    return BoardContext(snapshot=snap, profile=profile_for(w, h), width=w, height=h, fps=30, now=now, elapsed=t, event=ev)


def _in_bounds(img):
    left, top, right, bottom = img.getbbox()
    return left >= 0 and top >= 0 and right <= img.width and bottom <= img.height


def test_game_board_renders_every_phase():
    games = normalize_schedule(load("schedule_2026-09-03.json"))
    live = {**enrich_from_feed(by_id(games, 776002), load("feed_live_776002.json")), "favorite_side": "away"}
    variants = {
        "live": live,
        "break": {**by_id(games, 776006), "favorite_side": "home"},
        "delay": {**live, "situation": {**live["situation"], "delay": "RAIN DELAY"}},
        "nono": {**live, "situation": {**live["situation"], "no_hitter": True, "last_play": None}},
        "pregame": {**by_id(games, 776003), "favorite_side": "away"},
        "warmup": {**by_id(games, 776007), "favorite_side": "home"},
        "final": {**enrich_from_feed(by_id(games, 776001), load("feed_live_776001.json")), "favorite_side": "away"},
        "extras": {**by_id(games, 776004), "favorite_side": "away"},
        "ppd": {**by_id(games, 776005), "favorite_side": "home", "situation": {**by_id(games, 776005)["situation"], "delay": "RAIN DELAY"}},
        "spring": {**by_id(games, 776003), "game_type": "S", "type": 1, "series": "SPRING"},
        "alds": {**live, "game_type": "D", "type": 3, "series": "NLDS GM2"},
    }
    board = MlbGameBoard()
    for name, g in variants.items():
        snap = _snapshot(g)
        board.enter(_ctx(snap, 0.0), MlbGameConfig())
        frames = [board.render(_ctx(snap, t), MlbGameConfig()) for t in (0.0, 0.5, 2.0, 6.0)]
        assert all(f.size == (128, 64) and f.getbbox() is not None for f in frames), name
        assert frames[0].tobytes() != frames[2].tobytes(), name                 # entrances animate
        assert _in_bounds(frames[-1]), name
    # every toggle off still draws the base layout
    off = MlbGameConfig(show_records=False, show_bases=False, show_pitcher_batter=False, show_last_pitch=False, show_hits=False)
    for g in (live, variants["final"], variants["pregame"]):
        assert board.render(_ctx(_snapshot(g), 3.0), off).getbbox() is not None
    # small panel: best effort, but must not crash
    assert board.render(_ctx(_snapshot(live), 3.0, w=64, h=32), MlbGameConfig()).size == (64, 32)


def test_live_cluster_reflects_the_situation():
    games = normalize_schedule(load("schedule_2026-09-03.json"))
    live = {**by_id(games, 776002), "favorite_side": "away"}
    board = MlbGameBoard()
    cfg = MlbGameConfig()
    a = board.render(_ctx(_snapshot(live), 5.0), cfg)
    loaded = {**live, "situation": {**live["situation"], "runners": [True, True, True], "outs": 2, "balls": 3}}
    b = board.render(_ctx(_snapshot(loaded), 5.0), cfg)
    assert a.tobytes() != b.tobytes()
    cluster = (34, 40, 94, 54)
    assert a.crop(cluster).tobytes() != b.crop(cluster).tobytes()               # the change is in the count/bases/outs row
    bottom = {**live, "situation": {**live["situation"], "half": "bottom", "batting": "home"}}
    c = board.render(_ctx(_snapshot(bottom), 5.0), cfg)
    assert a.crop((34, 14, 94, 21)).tobytes() != c.crop((34, 14, 94, 21)).tobytes()      # arrow flips
    assert bases_image((True, False, True)).size == (17, 10) and bases_image((False, False, False)).getbbox() is not None


def test_other_boards_render():
    games = normalize_schedule(load("schedule_2026-09-03.json"))
    snap = _snapshot({**by_id(games, 776001), "favorite_side": "away"})
    for board, cfg in [(MlbTickerBoard(), TickerConfig()), (MlbTeamSummaryBoard(), TeamSummaryConfig()),
                       (MlbStandingsBoard(), StandingsConfig()), (MlbStandingsBoard(), StandingsConfig(view="wildcard")),
                       (MlbStandingsBoard(), StandingsConfig(view="league"))]:
        board.enter(_ctx(snap, 0.0), cfg)
        for t in (0.5, 3.0, 12.0):
            img = board.render(_ctx(snap, t), cfg)
            assert img.size == (128, 64) and img.getbbox() is not None, board.key
        assert board.auto_seconds(_ctx(snap, 0.0), cfg) > 0
    ticker = MlbTickerBoard()
    ticker.enter(_ctx(snap, 0.0), TickerConfig())
    assert len(ticker._games) == 7
    lines = MlbTeamSummaryBoard()._record_lines(snap.get("mlb.team_summary")["NYY"]["record"])
    assert lines == ["82-56  .594", "AL EAST 1ST", "L10 7-3"]
    st = MlbStandingsBoard()
    assert st._points({"games_back": "4.0"}) == "4.0" and st._record({"wins": 82, "losses": 56}) == "82-56"
    assert st.wildcard_cutoff == 3


def test_standings_final_banner_in_the_offseason():
    games = normalize_schedule(load("schedule_2026-09-03.json"))
    snap = _snapshot({**by_id(games, 776001), "favorite_side": "away"})
    store = SnapshotStore()
    for k, v in snap.data.items():
        store.publish(k, v)
    snap = store.publish("mlb.season", {"sport": "mlb", "phase": "offseason", "standings_final": True, "standings_season_id": 2026})
    assert MlbStandingsBoard()._banner(_ctx(snap, 0.0)) == "FINAL 2026"


def test_score_alert_board():
    games = normalize_schedule(load("schedule_2026-09-03.json"))
    live = {**enrich_from_feed(by_id(games, 776002), load("feed_live_776002.json")), "favorite_side": "away"}
    snap = _snapshot(live)
    hr = Event("mlb.home_run", team="LAD", payload={"side": "away", "game": live, "score": "4-2", "runs": 2, "batter": "Shohei Ohtani"})
    run = Event("mlb.run", team="SD", payload={"side": "home", "game": live, "score": "4-3", "runs": 1, "batter": "Manny Machado"})
    board = MlbScoreBoard()
    assert board.matches(hr, ScoreConfig()) and not board.matches(run, ScoreConfig())          # opponent runs are quiet by default
    assert board.matches(run, ScoreConfig(opponent_scores=True))
    assert not board.matches(Event("mlb.run", team="LAD", payload={"side": "away", "game": live}), ScoreConfig(runs=False))
    for ev in (hr, run):
        board = MlbScoreBoard()
        board.enter(_ctx(snap, 0.0, ev), ScoreConfig(opponent_scores=True))
        assert board.render(_ctx(snap, 1.0, ev), ScoreConfig(opponent_scores=True)).getbbox() is not None
        assert board.auto_seconds(_ctx(snap, 0.0, ev), ScoreConfig()) > 1


def test_season_countdown_picks_mlb():
    store = SnapshotStore()
    store.publish("mlb.team_summary", {"NYY": {"abbrev": "NYY"}})
    snap = store.publish("mlb.season", {"sport": "mlb", "phase": "preseason", "days_to_regular": 20, "regular_start": "2026-03-26",
                                        "first_game": {"date": "2026-03-26", "home": False, "opponent": "TOR"}})
    ctx = _ctx(snap, 1.0)
    ctx = replace(ctx, now=datetime(2026, 3, 6, 12, tzinfo=ZoneInfo("America/New_York")))
    board = SeasonCountdownBoard()
    assert board._pick(ctx, CountdownConfig())[0] == "mlb"
    assert board.render(ctx, CountdownConfig(sport="mlb")).getbbox() is not None


# -- source -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_publishes_everything(monkeypatch):
    import scoreboard.mlb.source as src
    monkeypatch.setattr(src, "_today", lambda ctx: TODAY)
    store = SnapshotStore()
    cfg = MlbConfig(favorites=["LAD", "NYY"], idle_interval=15, standings_interval=300)
    seen = {}

    def schedule(request):
        params = dict(request.url.params)
        seen.setdefault("schedule", []).append(params)
        if params.get("teamId") == "147":
            return httpx.Response(200, json=load("schedule_NYY_2026-08-24_2026-09-17.json"))
        if params.get("teamId"):
            return httpx.Response(200, json={"dates": []})
        assert params["startDate"] == TODAY and params["endDate"] == "2026-09-04" and "linescore" in params["hydrate"]
        return httpx.Response(200, json=load("schedule_2026-09-03.json"))

    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(url__regex=r"https://a\.espncdn\.com/.*").mock(return_value=httpx.Response(404))
        mock.get(f"{BASE_URL}/schedule").mock(side_effect=schedule)
        mock.get(f"{BASE_URL}/standings").mock(return_value=httpx.Response(200, json=load("standings_2026.json")))
        mock.get(f"{BASE_URL}/seasons").mock(return_value=httpx.Response(200, json=load("seasons_2026.json")))
        mock.get(f"{FEED_URL}/game/776002/feed/live").mock(return_value=httpx.Response(200, json=load("feed_live_776002.json")))
        ctx = SourceContext("mlb", store, lambda: cfg, http)
        task = asyncio.create_task(MlbSource().run(ctx))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if store.get().has("mlb.scores", "mlb.main_event", "mlb.standings", "mlb.team_summary", "mlb.season"):
                break
        task.cancel()
        snap = store.get()
    assert len(snap.get("mlb.scores")) == 7
    assert len(snap.get("mlb.schedule")) >= 7 and all(g["date"] == TODAY for g in snap.get("mlb.schedule"))   # today only by default
    main = snap.get("mlb.main_event")
    assert main["id"] == "776002" and main["favorite_side"] == "away" and main["situation"]["last_play"]["type"] == "home_run"
    assert snap.get("main_event")["sport"] == "mlb" if snap.get("main_event") else True
    assert snap.get("mlb.standings")["teams"]["LAD"]["wins"] == 85
    assert set(snap.get("mlb.team_summary")) == {"LAD", "NYY"} and snap.get("mlb.team_summary")["NYY"]["next_game"]["opponent"] == "TOR"
    assert snap.get("mlb.season")["phase"] == "regular" and snap.get("mlb.season")["favorite"] == "LAD"
    assert any(p.get("teamId") == "119" for p in seen["schedule"])          # favourite schedules by Stats API id


@pytest.mark.asyncio
async def test_source_survives_api_failures(monkeypatch):
    import scoreboard.mlb.api as api_mod
    import scoreboard.mlb.source as src
    monkeypatch.setattr(api_mod, "RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(src, "_today", lambda ctx: TODAY)
    store = SnapshotStore()
    cfg = MlbConfig(favorites=["NYY"], idle_interval=15)
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r"https://a\.espncdn\.com/.*").mock(return_value=httpx.Response(404))
        mock.get(f"{BASE_URL}/schedule").mock(return_value=httpx.Response(503))
        mock.get(f"{BASE_URL}/standings").mock(return_value=httpx.Response(503))
        mock.get(f"{BASE_URL}/seasons").mock(return_value=httpx.Response(503))
        ctx = SourceContext("mlb", store, lambda: cfg, http)
        task = asyncio.create_task(MlbSource().run(ctx))
        await asyncio.sleep(0.2)
        assert not task.done()                                     # loops keep going, nothing crashed
        task.cancel()
    assert not store.get().has("mlb.scores")


@pytest.mark.asyncio
async def test_spring_uses_last_seasons_table(monkeypatch):
    import scoreboard.mlb.source as src
    monkeypatch.setattr(src, "_today", lambda ctx: "2026-03-01")
    store = SnapshotStore()
    cfg = MlbConfig(favorites=[], idle_interval=15)
    years = []

    def standings(request):
        years.append(request.url.params["season"])
        if request.url.params["season"] == "2026":
            return httpx.Response(200, json={"records": [{"standingsType": "regularSeason", "teamRecords": []}]})
        return httpx.Response(200, json=load("standings_2026.json"))

    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(url__regex=r"https://a\.espncdn\.com/.*").mock(return_value=httpx.Response(404))
        mock.get(f"{BASE_URL}/schedule").mock(return_value=httpx.Response(200, json={"dates": []}))
        mock.get(f"{BASE_URL}/standings").mock(side_effect=standings)
        mock.get(f"{BASE_URL}/seasons").mock(return_value=httpx.Response(200, json=load("seasons_2026.json")))
        ctx = SourceContext("mlb", store, lambda: cfg, http)
        task = asyncio.create_task(MlbSource().run(ctx))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if store.get().has("mlb.season", "mlb.standings"):
                break
        task.cancel()
        snap = store.get()
    assert years[:2] == ["2026", "2025"]
    assert snap.get("mlb.season")["phase"] == "preseason" and snap.get("mlb.season")["standings_final"] and snap.get("mlb.season")["standings_season_id"] == 2025
    assert snap.get("mlb.scores") == [] and snap.get("mlb.main_event") is None
