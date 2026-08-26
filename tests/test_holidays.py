from datetime import date, datetime
from zoneinfo import ZoneInfo

from scoreboard.boards.base import BoardContext
from scoreboard.data import SnapshotStore
from scoreboard.extras.holidays.board import CountdownBoard, CountdownConfig
from scoreboard.extras.holidays.source import CustomHoliday, HolidaysConfig, upcoming
from scoreboard.render.profiles import profile_for


def test_upcoming_includes_public_and_custom_within_horizon():
    cfg = HolidaysConfig(country="US", horizon_days=40, custom=[CustomHoliday(name="Puck Drop", date="10-07"),
                                                                 CustomHoliday(name="Old", date="2020-01-01")])
    items = upcoming(cfg, date(2026, 12, 1))
    names = [i["name"] for i in items]
    assert "Christmas Day" in names
    assert [i["date"] for i in items] == sorted(i["date"] for i in items)
    xmas = next(i for i in items if i["name"] == "Christmas Day")
    assert xmas["days"] == 24 and xmas["image"] == "christmas_day.png"
    assert "Puck Drop" not in names           # Oct 7 is outside a Dec 1 + 40 day window
    assert all(0 <= i["days"] <= 40 for i in items)
    assert "Old" not in names


def test_disabled_and_today():
    cfg = HolidaysConfig(country="US", disabled=["Christmas Day"], custom=[CustomHoliday(name="Game Day", date="12-25")])
    items = upcoming(cfg, date(2026, 12, 25))
    assert [i["name"] for i in items if i["days"] == 0] == ["Game Day"]


def test_countdown_board_renders_and_cycles():
    snap = SnapshotStore().publish("holidays.upcoming", [
        {"name": "Christmas Day", "date": "2026-12-25", "days": 24, "image": "christmas_day.png", "custom": False},
        {"name": "Game Day", "date": "2026-12-01", "days": 0, "image": None, "custom": True},
    ])
    now = datetime(2026, 12, 1, tzinfo=ZoneInfo("America/Toronto"))
    board, cfg = CountdownBoard(), CountdownConfig(seconds_per_holiday=3)
    for w, h in [(128, 64), (64, 32)]:
        ctx = BoardContext(snapshot=snap, profile=profile_for(w, h), width=w, height=h, fps=30, now=now, elapsed=1.0)
        first = board.render(ctx, cfg)
        assert first.size == (w, h) and first.getbbox() is not None
        second = board.render(BoardContext(**{**ctx.__dict__, "elapsed": 4.0}), cfg)
        assert first.tobytes() != second.tobytes()
        assert board.done(BoardContext(**{**ctx.__dict__, "elapsed": 6.1}), cfg)
