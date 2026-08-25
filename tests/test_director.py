from datetime import UTC, datetime

from PIL import Image

from scoreboard.boards.base import EventBoard
from scoreboard.boards.blank import BlankBoard
from scoreboard.boards.clock import ClockBoard
from scoreboard.boards.splash import SplashBoard
from scoreboard.config import ConfigStore
from scoreboard.config.models import BrightnessConfig, LocationConfig
from scoreboard.data import SnapshotStore
from scoreboard.data.events import Event, EventBus
from scoreboard.director import AppState, Director, compute_state
from scoreboard.director.brightness import brightness_for
from scoreboard.director.director import BOOT_SECONDS
from scoreboard.plugins import Registry


class GoalBoard(EventBoard):
    key = "goal"
    title = "Goal"
    event_kinds = frozenset({"goal"})

    def render(self, ctx, cfg):
        return Image.new("RGB", (ctx.width, ctx.height), (255, 0, 0))

    def done(self, ctx, cfg):
        return ctx.elapsed >= 2.0


def booted(d, t0=1000.0):
    """Advance a fresh director past the boot splash; returns the current time."""
    d.frame(t0)
    t = t0 + BOOT_SECONDS + 0.1
    d.frame(t)
    assert d.state == AppState.OFFDAY
    return t


def make(tmp_path):
    config = ConfigStore(tmp_path / "config.json")
    config.update({"playlists": {"offday": [{"board": "clock", "duration": 5}, {"board": "blank", "duration": 5}]}})
    snapshots, events = SnapshotStore(), EventBus()
    snapshots.subscribe(events.on_snapshot)
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard(), BlankBoard(), GoalBoard())})
    return config, snapshots, events, Director(config, snapshots, reg, events)


def test_compute_state_from_snapshot():
    store = SnapshotStore()
    assert compute_state(store.get()) == AppState.OFFDAY
    assert compute_state(store.publish("main_event", {"phase": "live"})) == AppState.LIVE
    assert compute_state(store.publish("main_event", {"phase": "intermission"})) == AppState.INTERMISSION
    assert compute_state(store.publish("system", {"online": False})) == AppState.ERROR


def test_boot_then_playlist_rotation(tmp_path):
    _, _, _, d = make(tmp_path)
    t0 = 1000.0
    d.frame(t0)
    assert d.state == AppState.BOOT and d.active_board == "splash"
    d.frame(t0 + BOOT_SECONDS + 0.1)
    assert d.state == AppState.OFFDAY and d.active_board == "clock"
    d.frame(t0 + BOOT_SECONDS + 5.2)          # clock duration 5s expired -> advances
    d.frame(t0 + BOOT_SECONDS + 5.3)
    assert d.active_board == "blank"
    d.frame(t0 + BOOT_SECONDS + 10.5)
    d.frame(t0 + BOOT_SECONDS + 10.6)
    assert d.active_board == "clock"


def test_event_interrupts_then_resumes(tmp_path):
    _, snapshots, events, d = make(tmp_path)
    t = booted(d)
    assert d.active_board == "clock"
    events._queue.append(Event("goal", team="TOR", ts=t))
    frame = d.frame(t + 0.1)
    assert d.active_board == "goal"
    assert frame.getpixel((0, 0)) == (255, 0, 0)
    d.frame(t + 2.5)                          # goal board reports done
    d.frame(t + 2.6)
    assert d.active_board == "clock"


def test_state_change_resets_playlist(tmp_path):
    _, snapshots, _, d = make(tmp_path)
    t = booted(d)
    snapshots.publish("main_event", {"phase": "live"})
    d.frame(t + 0.1)
    assert d.state == AppState.LIVE


def test_config_edit_applies_next_frame(tmp_path):
    config, _, _, d = make(tmp_path)
    t = booted(d)
    config.update({"playlists": {"offday": [{"board": "blank", "duration": 5}]}})
    d.frame(t + 0.1)
    assert d.active_board == "blank"


def test_brightness_modes():
    loc = LocationConfig(latitude=43.65, longitude=-79.38, timezone="America/Toronto")
    hours = BrightnessConfig(mode="hours", day=90, night=10, night_start="22:00", night_end="07:00")
    assert brightness_for(datetime(2026, 1, 1, 23, 0, tzinfo=UTC), hours, loc, live=False) == 10
    assert brightness_for(datetime(2026, 1, 1, 12, 0, tzinfo=UTC), hours, loc, live=False) == 90
    assert brightness_for(datetime(2026, 1, 1, 23, 0, tzinfo=UTC), hours, loc, live=True) == 90
    sun = BrightnessConfig(mode="sun", day=80, night=20)
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Toronto")
    assert brightness_for(datetime(2026, 6, 21, 13, 0, tzinfo=tz), sun, loc, live=False) == 80
    assert brightness_for(datetime(2026, 6, 21, 23, 30, tzinfo=tz), sun, loc, live=False) == 20
