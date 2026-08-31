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
    assert compute_state(store.publish("system", {"online": False})) == AppState.INTERMISSION   # offline but has data
    assert compute_state(SnapshotStore().publish("system", {"online": False})) == AppState.ERROR  # offline, nothing to show


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


def test_transition_blends_between_playlist_boards(tmp_path):
    from scoreboard.director.transitions import STYLES, transition

    red, blue = Image.new("RGB", (8, 4), (255, 0, 0)), Image.new("RGB", (8, 4), (0, 0, 255))
    assert transition("fade", red, blue, 0.5).getpixel((0, 0)) not in ((255, 0, 0), (0, 0, 255))
    assert transition("wipe", red, blue, 0.5).getpixel((0, 0)) == (0, 0, 255)
    assert transition("wipe", red, blue, 0.5).getpixel((7, 0)) == (255, 0, 0)
    for style in STYLES:
        assert transition(style, red, blue, 1.0) is blue and transition(style, red, blue, 0.0).size == (8, 4)

    config, _, _, d = make(tmp_path)
    config.update({"transition": {"style": "fade", "duration": 1.0},
                   "playlists": {"offday": [{"board": "clock", "duration": 2}, {"board": "blank", "duration": 2}]}})
    t = booted(d)
    d.frame(t + 2.1)                       # clock duration expired -> cursor advances
    d.frame(t + 2.2)                       # switch to blank happens here, transition starts
    mid = d.frame(t + 2.7)                 # halfway through a 1s fade from clock to black
    assert d.active_board == "blank"
    assert mid.getbbox() is not None       # still shows fading clock pixels
    settled = d.frame(t + 3.5)
    assert settled.getbbox() is None       # pure blank after the transition


def test_no_transition_into_event_boards(tmp_path):
    config, snapshots, events, d = make(tmp_path)
    config.update({"transition": {"style": "fade", "duration": 1.0}})
    t = booted(d)
    events._queue.append(Event("goal", team="TOR", ts=t))
    frame = d.frame(t + 0.1)
    assert d.active_board == "goal" and frame.getpixel((0, 0)) == (255, 0, 0)   # instant, no fade


class BrokenBoard(BlankBoard):
    key = "broken"
    title = "Broken"

    def render(self, ctx, cfg):
        raise RuntimeError("boom")


def test_broken_board_is_quarantined_and_rotation_continues(tmp_path):
    config = ConfigStore(tmp_path / "config.json")
    config.update({"transition": {"style": "none"},
                   "playlists": {"offday": [{"board": "broken", "duration": 5}, {"board": "blank", "duration": 5}]}})
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard(), BlankBoard(), BrokenBoard())})
    d = Director(config, snapshots, reg, events)
    t = booted(d)
    d.frame(t + 0.1)
    assert d.active_board == "blank"                       # skipped past the broken board
    for i in range(1, 30):
        d.frame(t + 0.1 + i)
    assert d.active_board == "blank"                       # never returns to it inside the quarantine window


def test_stale_marker_when_offline_with_data(tmp_path):
    _, snapshots, _, d = make(tmp_path)
    t = booted(d)
    snapshots.publish("nhl.scores", [])                    # we have (some) data
    snapshots.publish("system", {"online": False})
    frame = d.frame(t + 0.1)
    w, h = frame.size
    assert frame.getpixel((w - 2, h - 2)) == (200, 40, 40)
    assert d.state == AppState.OFFDAY                      # still showing data, not the error clock


def test_board_with_empty_required_data_is_skipped(tmp_path):

    class NeedsData(BlankBoard):
        key = "needs"
        title = "Needs data"
        requires = frozenset({"things"})

    config = ConfigStore(tmp_path / "config.json")
    config.update({"transition": {"style": "none"}, "playlists": {"offday": [{"board": "needs", "duration": 5}, {"board": "clock", "duration": 5}]}})
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard(), BlankBoard(), NeedsData())})
    d = Director(config, snapshots, reg, events)
    t = booted(d)
    snapshots.publish("things", [])
    d.frame(t + 0.1)
    assert d.active_board == "clock"            # empty list -> skipped
    snapshots.publish("things", [1])
    d.frame(t + 5.2); d.frame(t + 5.3)
    assert d.active_board == "needs"


def test_a_missing_fallback_board_still_draws_something(tmp_path):
    """`plugins._load` swallows a broken entry point, so the board everything falls back
    to can simply be absent — and it was the one lookup that assumed it never would be.
    A KeyError every frame is caught by the render loop, which means a black panel on a
    service that still reports itself healthy. Draw an empty frame instead."""
    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={})                                   # nothing loaded at all
    d = Director(config, snapshots, reg, events)
    for t in (1000.0, 1000.0 + BOOT_SECONDS + 0.1):
        frame = d.frame(t)
        assert frame.size == (config.get().display.width, config.get().display.height)
    snapshots.publish("system", {"online": False})              # drives the ERROR branch too
    assert d.frame(1100.0).size == (128, 64)


def test_a_board_that_only_the_error_state_needs_is_not_assumed(tmp_path):
    """Same lookup, reached by the offline path rather than the empty-playlist path."""
    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    snapshots.publish("system", {"online": False})
    d = Director(config, snapshots, Registry(boards={"splash": SplashBoard()}), events)
    d.frame(1000.0)
    assert d.frame(1000.0 + BOOT_SECONDS + 0.1).size == (128, 64)
