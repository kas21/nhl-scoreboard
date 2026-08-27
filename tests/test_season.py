import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scoreboard.boards.base import BoardContext
from scoreboard.boards.season_countdown import CountdownConfig, SeasonCountdownBoard, milestone
from scoreboard.data import SnapshotStore
from scoreboard.director import AppState, compute_state
from scoreboard.nhl.boards.standings import StandingsBoard, StandingsConfig
from scoreboard.nhl.normalize import normalize_standings
from scoreboard.nhl.season import season_info
from scoreboard.render.profiles import profile_for

FIX = Path(__file__).parent / "fixtures" / "nhl"
SCHED = json.loads((FIX / "schedule_now.json").read_text())
CLUB = json.loads((FIX / "club_schedule_TOR_week.json").read_text())


def test_season_phases():
    assert season_info(SCHED, date(2026, 8, 27))["phase"] == "offseason"
    assert season_info(SCHED, date(2026, 9, 20))["phase"] == "preseason"
    assert season_info(SCHED, date(2026, 12, 1))["phase"] == "regular"
    assert season_info(SCHED, date(2027, 5, 1))["phase"] == "playoffs"
    info = season_info(SCHED, date(2026, 8, 27), standings_season_id=20252026)
    assert info["days_to_regular"] == 33 and info["days_to_preseason"] == 23
    assert info["standings_final"] and info["season_id"] == 20262027


def test_first_game_from_club_schedule():
    club = {"currentSeason": 20262027, "games": [{"gameType": 1, "gameDate": "2026-09-20", "homeTeam": {"abbrev": "TOR"}, "awayTeam": {"abbrev": "OTT"}},
                                                {"gameType": 2, "gameDate": "2026-10-08", "homeTeam": {"abbrev": "TOR"}, "awayTeam": {"abbrev": "MTL"}, "startTimeUTC": "2026-10-08T23:00:00Z"}]}
    info = season_info(SCHED, date(2026, 8, 27), 20252026, club, "TOR")
    assert info["first_game"] == {"date": "2026-10-08", "home": True, "opponent": "MTL", "start_time_utc": "2026-10-08T23:00:00Z"}
    m = milestone({**info, "favorite": "TOR"}, date(2026, 8, 27))
    assert m["days"] == 42 and m["label"] == "OPENER VS MTL"
    assert milestone(season_info(SCHED, date(2026, 12, 1)), date(2026, 12, 1)) is None


def test_offseason_state_and_countdown_board():
    store = SnapshotStore()
    assert compute_state(store.get()) == AppState.OFFDAY                      # no season info yet
    snap = store.publish("nhl.season", {**season_info(SCHED, date(2026, 8, 27), 20252026), "favorite": "TOR"})
    assert compute_state(snap) == AppState.OFFSEASON
    snap = store.publish("nfl.season", {"sport": "nfl", "phase": "preseason"})
    assert compute_state(snap) == AppState.OFFDAY                             # one sport is in season
    now = datetime(2026, 8, 27, 12, tzinfo=ZoneInfo("America/Toronto"))
    ctx = BoardContext(snapshot=snap, profile=profile_for(128, 64), width=128, height=64, fps=30, now=now, elapsed=2.0)
    board = SeasonCountdownBoard()
    assert not board.done(ctx, CountdownConfig())
    assert board.render(ctx, CountdownConfig()).getbbox() is not None
    in_season = store.publish("nhl.season", season_info(SCHED, date(2026, 12, 1)))
    in_season = store.publish("nfl.season", {"sport": "nfl", "phase": "regular"})
    assert board.done(BoardContext(**{**ctx.__dict__, "snapshot": in_season}), CountdownConfig())


def test_standings_final_banner():
    st = normalize_standings(json.loads((FIX / "standings_2026-04-10.json").read_text()))
    store = SnapshotStore(); store.publish("nhl.standings", st)
    snap = store.publish("nhl.season", season_info(SCHED, date(2026, 8, 27), 20252026))
    now = datetime(2026, 8, 27, 12, tzinfo=ZoneInfo("America/Toronto"))
    ctx = BoardContext(snapshot=snap, profile=profile_for(128, 64), width=128, height=64, fps=30, now=now, elapsed=0.5)
    b = StandingsBoard()
    b.enter(ctx, StandingsConfig())
    assert b._banner(ctx) == "FINAL 2025-26"
    img = b.render(ctx, StandingsConfig())
    assert any(px == (255, 200, 0) for px in [img.getpixel((x, 3)) for x in range(0, 128, 4)])
