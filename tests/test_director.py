from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image

from scoreboard.boards.base import AUTO_SECONDS, EventBoard
from scoreboard.boards.blank import BlankBoard
from scoreboard.boards.clock import ClockBoard
from scoreboard.boards.splash import SplashBoard
from scoreboard.config import ConfigStore
from scoreboard.config.models import BrightnessConfig, LocationConfig, PlaylistEntry
from scoreboard.data import SnapshotStore
from scoreboard.data.events import Event, EventBus
from scoreboard.director import AppState, Director, compute_state
from scoreboard.director.brightness import brightness_for
from scoreboard.director.director import BOOT_SECONDS
from scoreboard.director.playlist import available_entries
from scoreboard.director.state import dormant_sports, season_over_days
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


def test_event_boards_in_a_playlist_are_skipped(tmp_path):
    """An interrupt board only makes sense with an event behind it; listed in a playlist
    (an old config, a hand edit) it is passed over rather than drawn empty."""
    config, snapshots, events, d = make(tmp_path)
    config.update({"transition": {"style": "none"},
                   "playlists": {"offday": [{"board": "goal", "duration": 5}, {"board": "blank", "duration": 5}]}})
    t = booted(d)
    assert d.active_board == "blank"
    d.frame(t + 5.2); d.frame(t + 5.3)
    assert d.active_board == "blank"                                # only one real entry: it just stays
    assert available_entries((PlaylistEntry(board="goal"), PlaylistEntry(board="blank")), {"goal", "blank"}, {"goal"}) == [PlaylistEntry(board="blank")]
    assert GoalBoard.playlistable is False and BlankBoard.playlistable is True


def test_interrupt_boards_in_a_playlist_are_warned_about_on_every_config_change(tmp_path, caplog):
    config, snapshots, events, d = make(tmp_path)
    with caplog.at_level("WARNING", logger="scoreboard.director.director"):
        config.update({"playlists": {"offday": [{"board": "goal", "duration": 5}, {"board": "blank", "duration": 5}]}})
        config.update({"playlists": {"offday": [{"board": "blank", "duration": 5}]}})
        config.update({"playlists": {"offday": [{"board": "goal", "duration": 5}, {"board": "blank", "duration": 5}]}})
        config.update({"brightness": {"day": 50}})                      # playlists untouched: no repeat
    assert sum("goal" in r.message and "offday" in r.message for r in caplog.records) == 2


class RecordingGoalBoard(GoalBoard):
    """Remembers the event it was entered with, like a SequenceMixin board caching its timeline."""

    def __init__(self):
        self.entered = []

    def enter(self, ctx, cfg):
        self.entered.append(ctx.event.payload.get("n") if ctx.event else None)


def test_a_second_event_on_the_same_board_re_enters_it(tmp_path, monkeypatch):
    """Two goals a few seconds apart: the second must not replay the first one's timeline."""
    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    goal = RecordingGoalBoard()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard(), BlankBoard(), goal)})
    d = Director(config, snapshots, reg, events)
    t = booted(d)
    events._queue.append(Event("goal", payload={"n": 1}))
    d.frame(t + 0.1)
    events._queue.append(Event("goal", payload={"n": 2}))
    d.frame(t + 1.0)
    assert goal.entered == [1] and d.active_board == "goal"
    d.frame(t + 2.2); d.frame(t + 2.3)      # first goal done -> the pending second is picked up
    assert d.active_board == "goal" and goal.entered == [1, 2]
    d.frame(t + 4.5); d.frame(t + 4.6)
    assert d.active_board == "clock"
    wall = {"now": t + 5.0}                 # the override expiry reads the real clock; pin it to the frame times
    monkeypatch.setattr("scoreboard.director.director._time.monotonic", lambda: wall["now"])
    d.set_override("goal", 5.0)             # the UI previews the board with no event behind it
    d.frame(t + 5.0)
    assert goal.entered == [1, 2, None]
    events._queue.append(Event("goal", payload={"n": 3}))
    d.frame(t + 6.0)
    assert goal.entered == [1, 2, None]     # override still holds
    wall["now"] = t + 10.5
    d.frame(t + 10.5)                       # override lapsed: the real event plays, freshly entered
    assert d.active_board == "goal" and goal.entered == [1, 2, None, 3]


def test_rotation_view_lists_every_entry_with_why_it_is_skipped(tmp_path):
    """The dashboard draws the lap from this, so it has to say exactly what the frame loop does:
    fixed lengths, what "auto" works out to, the cursor, and each passed-over entry's reason."""

    class NeedsData(BlankBoard):
        key = "needs"
        title = "Needs data"
        requires = frozenset({"things"})

        def auto_seconds(self, ctx, cfg):
            return 4.0 * len(ctx.snapshot.get("things") or [])

        def auto_items(self, ctx, cfg):
            return len(ctx.snapshot.get("things") or []), "thing"

    config = ConfigStore(tmp_path / "config.json")
    config.update({"transition": {"style": "none"}, "playlists": {"offday": [
        {"board": "clock", "duration": 5},
        {"board": "needs", "duration": None},
        {"board": "blank", "duration": 7, "enabled": False},
        {"board": "goal", "duration": 5},
        {"board": "missing", "duration": 5},
    ]}})
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard(), BlankBoard(), NeedsData(), GoalBoard())})
    d = Director(config, snapshots, reg, events)

    assert d.rotation(0.0)["entries"] == [] and d.rotation(0.0)["state"] == "boot"    # boot has no playlist
    t = booted(d)
    rot = d.rotation(t + 2.0)
    assert rot["state"] == "offday" and rot["board"] == "clock" and rot["index"] == 0 and rot["event"] is None
    by = {e["board"]: e for e in rot["entries"]}
    assert [e["board"] for e in rot["entries"]] == ["clock", "needs", "blank", "goal", "missing"]     # config order, nothing dropped
    assert by["clock"] == {**by["clock"], "seconds": 5, "auto": False, "skipped": None, "active": True, "count": None}
    assert abs(by["clock"]["elapsed"] - 2.0) < 1e-6
    assert by["needs"]["skipped"] == "no data" and by["needs"]["seconds"] is None        # not probed while it cannot play
    assert by["blank"]["skipped"] == "disabled"
    assert by["goal"]["skipped"] == "interrupt board, plays on its event"
    assert by["missing"]["skipped"] == "not loaded" and by["missing"]["title"] == "missing"
    assert rot["lap_seconds"] == 5

    snapshots.publish("things", [1, 2, 3])
    rot = d.rotation(t + 2.0)
    needs = next(e for e in rot["entries"] if e["board"] == "needs")
    assert needs == {**needs, "skipped": None, "seconds": 12.0, "auto": True, "count": 3, "unit": "thing", "active": False, "elapsed": None}
    assert rot["lap_seconds"] == 17.0

    d.frame(t + 5.2); d.frame(t + 5.3)                     # clock's 5s are up -> cursor moves to the auto entry
    rot = d.rotation(t + 6.3)
    assert rot["index"] == 1 and rot["board"] == "needs"
    assert [e["active"] for e in rot["entries"]] == [False, True, False, False, False]
    assert abs(next(e for e in rot["entries"] if e["active"])["elapsed"] - 1.0) < 1e-6


def test_rotation_view_reports_an_event_and_an_override_instead_of_a_cursor(tmp_path):
    _, _, events, d = make(tmp_path)
    t = booted(d)
    events._queue.append(Event("goal", team="TOR", ts=t))
    d.frame(t + 0.1)
    rot = d.rotation(t + 1.1)
    assert rot["board"] == "goal" and rot["index"] is None and not any(e["active"] for e in rot["entries"])
    assert rot["event"] == {**rot["event"], "board": "goal", "title": "Goal", "kind": "goal"} and abs(rot["event"]["elapsed"] - 1.0) < 1e-6
    d.frame(t + 2.5); d.frame(t + 2.6)
    assert d.rotation(t + 2.6)["event"] is None and d.rotation(t + 2.6)["index"] == 0

    d.set_override("blank", 30)
    d.frame(t + 3.0)
    rot = d.rotation(t + 3.0)
    assert rot["override"] == "blank" and rot["index"] is None and rot["board"] == "blank"


def test_rotation_view_marks_a_board_paused_after_an_error(tmp_path):
    class Broken(BlankBoard):
        key = "broken"
        title = "Broken"

        def render(self, ctx, cfg):
            raise RuntimeError("boom")

    config = ConfigStore(tmp_path / "config.json")
    config.update({"transition": {"style": "none"}, "playlists": {"offday": [{"board": "broken", "duration": 5}, {"board": "clock", "duration": 5}]}})
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard(), Broken())})
    d = Director(config, snapshots, reg, events)
    t = booted(d)
    d.frame(t + 0.1)
    by = {e["board"]: e for e in d.rotation(t + 0.2)["entries"]}
    assert by["broken"]["skipped"] == "paused after an error" and by["clock"]["active"]


class PacedBoard(BlankBoard):
    """Stands in for a ticker: three items, ctx.pace seconds each (2 s by default)."""
    key = "paced"
    title = "Paced"
    pace_unit = "thing"

    def _per(self, ctx):
        return 2.0 if ctx.pace is None else ctx.pace

    def done(self, ctx, cfg):
        return ctx.elapsed >= self._per(ctx) * 3

    def auto_seconds(self, ctx, cfg):
        return self._per(ctx) * 3

    def auto_items(self, ctx, cfg):
        return 3, "thing"


def _paced_director(tmp_path, duration):
    config = ConfigStore(tmp_path / "config.json")
    config.update({"transition": {"style": "none"}, "playlists": {"offday": [{"board": "paced", "duration": duration}, {"board": "clock", "duration": 5}]}})
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard(), PacedBoard())})
    return config, Director(config, snapshots, reg, events)


def test_a_number_on_a_paced_board_is_seconds_per_item_not_a_cap(tmp_path):
    """Kevin's model: 15 s on a ticker with 3 games means 45 s, every game shown."""
    _, d = _paced_director(tmp_path, 5)
    t = booted(d)
    assert d.active_board == "paced"
    d.frame(t + 5.5); d.frame(t + 5.6)            # a cap of 5 s would have moved on here
    assert d.active_board == "paced"
    d.frame(t + 14.9)
    assert d.active_board == "paced"
    d.frame(t + 15.1); d.frame(t + 15.2)          # 3 items x 5 s
    assert d.active_board == "clock"
    rot = d.rotation(t + 1.0)
    paced = rot["entries"][0]
    assert paced == {**paced, "pace_unit": "thing", "duration": 5, "seconds": 15.0, "count": 3, "auto": False}
    assert rot["lap_seconds"] == 20.0


def test_a_blank_on_a_paced_board_uses_its_own_pace(tmp_path):
    _, d = _paced_director(tmp_path, None)
    t = booted(d)
    d.frame(t + 6.1); d.frame(t + 6.2)            # 3 items x the board's default 2 s
    assert d.active_board == "clock"
    assert d.rotation(t + 0.5)["entries"][0]["seconds"] == 6.0


def test_a_number_on_an_unpaced_board_is_still_the_whole_run(tmp_path):
    _, _, _, d = make(tmp_path)                    # clock 5 s, blank 5 s
    t = booted(d)
    d.frame(t + 5.2); d.frame(t + 5.3)
    assert d.active_board == "blank"


def test_a_blank_on_a_board_with_no_length_of_its_own_is_the_default_run(tmp_path):
    """The bench Pi sat on the clock for an hour (2026-09-29): its off-day entry was blank and
    the clock never ends itself. Blank now means the default run when there is anywhere to go."""
    config, _, _, d = make(tmp_path)
    config.update({"playlists": {"offday": [{"board": "clock", "duration": None}, {"board": "blank", "duration": 5}]}})
    t = booted(d)
    assert d.active_board == "clock"
    d.frame(t + AUTO_SECONDS - 0.5)
    assert d.active_board == "clock"
    d.frame(t + AUTO_SECONDS + 0.1); d.frame(t + AUTO_SECONDS + 0.2)
    assert d.active_board == "blank"
    clock = d.rotation(t + 1.0)["entries"][0]
    assert clock == {**clock, "auto": True, "seconds": AUTO_SECONDS, "duration": None}


def test_a_blank_board_alone_in_its_playlist_holds_the_screen(tmp_path):
    """What the live game board wants: nothing else to show, so nothing to move on to, and no
    re-entry every 15 s to restart its animations."""
    config, _, _, d = make(tmp_path)
    config.update({"playlists": {"offday": [{"board": "clock", "duration": None}]}})
    t = booted(d)
    d.frame(t + AUTO_SECONDS + 0.5); d.frame(t + AUTO_SECONDS + 0.6)
    assert d.active_board == "clock"
    assert d.rotation(t + AUTO_SECONDS + 1.0)["entries"][0]["elapsed"] > AUTO_SECONDS
    alone = d.rotation(t + 1.0)["entries"][0]
    assert alone == {**alone, "seconds": None, "hold": True}


class HoldsUnlessSkipped(BlankBoard):
    """A board that overrides ``done`` only to skip itself — the season countdown's shape
    before 2026-09-29, when the bench Pi sat on "3 days til puck drop" for five minutes."""
    key = "holdy"
    title = "Holdy"

    def done(self, ctx, cfg):
        return not ctx.snapshot.get("system")


def test_a_board_that_only_ever_skips_itself_still_gets_the_default_run(tmp_path):
    config = ConfigStore(tmp_path / "config.json")
    config.update({"playlists": {"offday": [{"board": "holdy", "duration": None}, {"board": "clock", "duration": 5}]}})
    snapshots, events = SnapshotStore(), EventBus()
    snapshots.publish("system", {"online": True})
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard(), HoldsUnlessSkipped())})
    d = Director(config, snapshots, reg, events)
    t = booted(d)
    assert d.active_board == "holdy"
    d.frame(t + AUTO_SECONDS - 0.5)
    assert d.active_board == "holdy"
    d.frame(t + AUTO_SECONDS + 0.1); d.frame(t + AUTO_SECONDS + 0.2)
    assert d.active_board == "clock"


def test_keep_bright_when_live_covers_the_intermission(tmp_path):
    """Dimming for twenty minutes between periods and popping back up for the next one is
    not what "keep bright when live" means."""
    config, snapshots, _, d = make(tmp_path)
    config.update({"brightness": {"mode": "hours", "day": 90, "night": 10, "night_start": "18:00", "night_end": "08:00"}})
    night = datetime(2026, 1, 1, 23, 0, tzinfo=UTC)
    t = booted(d)
    assert d.brightness(night) == 10
    snapshots.publish("main_event", {"phase": "live"}); d.frame(t + 1)
    assert d.state == AppState.LIVE and d.brightness(night) == 90
    snapshots.publish("main_event", {"phase": "intermission"}); d.frame(t + 2)
    assert d.state == AppState.INTERMISSION and d.brightness(night) == 90
    snapshots.publish("main_event", {"phase": "postgame"}); d.frame(t + 3)
    assert d.brightness(night) == 10


def test_a_game_day_is_gameday_until_the_pregame_window_opens():
    store = SnapshotStore()
    snap = store.publish("main_event", {"phase": "pregame", "start_time_utc": "2026-04-11T23:00:00Z"})
    morning = datetime(2026, 4, 11, 14, 0, tzinfo=UTC)
    assert compute_state(snap, morning) == AppState.GAMEDAY
    assert compute_state(snap, datetime(2026, 4, 11, 21, 59, tzinfo=UTC)) == AppState.GAMEDAY
    assert compute_state(snap, datetime(2026, 4, 11, 22, 0, tzinfo=UTC)) == AppState.PREGAME     # exactly an hour out
    assert compute_state(snap, datetime(2026, 4, 11, 23, 30, tzinfo=UTC)) == AppState.PREGAME    # start passed, not live yet
    assert compute_state(snap, morning, pregame_hours=12) == AppState.PREGAME                     # the window is configurable
    assert compute_state(snap, datetime(2026, 4, 11, 22, 59, tzinfo=UTC), pregame_hours=0) == AppState.GAMEDAY
    # A game with no readable start time is pregame all day, as before the split.
    assert compute_state(store.publish("main_event", {"phase": "pregame", "start_time_utc": ""}), morning) == AppState.PREGAME
    assert compute_state(store.publish("main_event", {"phase": "live", "start_time_utc": "2026-04-11T23:00:00Z"}), morning) == AppState.LIVE


def test_director_moves_from_gameday_to_pregame_on_the_clock(tmp_path):
    config, store, _, d = make(tmp_path)
    config.update({"transition": {"style": "none"}, "sports": {"pregame_hours": 2},
                   "playlists": {"gameday": [{"board": "blank", "duration": 5}], "pregame": [{"board": "clock", "duration": 5}]}})
    store.publish("main_event", {"phase": "pregame", "start_time_utc": (datetime.now(UTC) + timedelta(hours=3)).isoformat()})
    d.frame(1000.0)
    d.frame(1000.0 + BOOT_SECONDS + 0.1)
    assert d.state == AppState.GAMEDAY and d.active_board == "blank"
    store.publish("main_event", {"phase": "pregame", "start_time_utc": (datetime.now(UTC) + timedelta(minutes=90)).isoformat()})
    d.frame(1000.0 + BOOT_SECONDS + 0.2)
    assert d.state == AppState.PREGAME and d.active_board == "clock"


def test_days_since_a_season_ended():
    today = date(2026, 11, 6)
    assert season_over_days(None, today) is None and season_over_days({"phase": "regular", "ended": "2026-10-31"}, today) is None
    assert season_over_days({"phase": "offseason", "ended": "2026-10-31"}, today) == 6
    assert season_over_days({"phase": "offseason", "ended": "2026-11-06"}, today) == 0
    # The feed has moved on to next season's dates (or never said): long over, not "just ended".
    assert season_over_days({"phase": "offseason", "ended": None}, today) > 10_000
    assert season_over_days({"phase": "offseason"}, today) > 10_000
    assert season_over_days({"phase": "offseason", "ended": "soon"}, today) > 10_000


def test_dormant_sports_are_the_ones_past_the_grace_period():
    today = date(2026, 11, 6)
    store = SnapshotStore()
    store.publish("mlb.season", {"phase": "offseason", "ended": "2026-10-31"})         # six days ago
    store.publish("nfl.season", {"phase": "offseason"})                               # no date: long over
    store.publish("ahl.season", {"phase": "preseason"})
    snap = store.publish("nhl.season", {"phase": "regular"})
    assert dormant_sports(snap, today, 7) == {"nfl"}
    assert dormant_sports(snap, today, 6) == {"nfl"}                                  # still inside the last day
    assert dormant_sports(snap, today, 5) == {"mlb", "nfl"}
    assert dormant_sports(snap, today, 0) == {"mlb", "nfl"}
    assert dormant_sports(snap, today, None) == frozenset()                           # switched off
    # A game of its own today (the Simulator's, or a feed late to notice opening day) wakes a sport up.
    snap = store.publish("nfl.main_event", {"phase": "live"})
    assert dormant_sports(snap, today, 5) == {"mlb"}
    assert dormant_sports(store.publish("nfl.main_event", None), today, 5) == {"mlb", "nfl"}


def test_season_boards_leave_the_rotation_once_their_sport_is_long_over(tmp_path):
    class Summary(BlankBoard):
        key = "mlb.team_summary"
        title = "MLB team summary"
        requires = frozenset({"mlb.team_summary"})
        seasonal = True

    class Standings(Summary):
        key = "nhl.standings"
        title = "NHL standings"
        requires = frozenset({"nhl.standings"})

    class Ticker(BlankBoard):               # not seasonal: it follows the slate
        key = "mlb.ticker"
        title = "MLB ticker"

    config = ConfigStore(tmp_path / "config.json")
    config.update({"transition": {"style": "none"}, "playlists": {"offday": [
        {"board": "mlb.team_summary", "duration": 5}, {"board": "nhl.standings", "duration": 5},
        {"board": "mlb.ticker", "duration": 5}, {"board": "clock", "duration": 5}]}})
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard(), Summary(), Standings(), Ticker())})
    d = Director(config, snapshots, reg, events)
    today = datetime.now(ZoneInfo(config.get().location.timezone)).date()
    snapshots.publish("mlb.team_summary", {"NYY": {}})
    snapshots.publish("nhl.standings", {"teams": {"TOR": {}}})
    snapshots.publish("nhl.season", {"phase": "regular"})
    snapshots.publish("mlb.season", {"phase": "offseason", "ended": (today - timedelta(days=3)).isoformat()})
    t = booted(d)

    def skipped(at):
        return {e["board"]: e["skipped"] for e in d.rotation(at)["entries"]}

    assert config.get().sports.offseason_hide_after_days == 7
    assert set(skipped(t).values()) == {None} and d.active_board == "mlb.team_summary"    # three days on: still up

    snapshots.publish("mlb.season", {"phase": "offseason", "ended": (today - timedelta(days=30)).isoformat()})
    d.frame(t + 0.1)
    assert skipped(t + 0.1) == {"mlb.team_summary": "off-season", "nhl.standings": None, "mlb.ticker": None, "clock": None}
    assert d.active_board == "nhl.standings"                                              # hockey is in season

    config.update({"sports": {"offseason_hide_after_days": None}})                        # blank: never hide
    assert skipped(t + 0.2)["mlb.team_summary"] is None
    config.update({"sports": {"offseason_hide_after_days": 60}})
    assert skipped(t + 0.2)["mlb.team_summary"] is None
    config.update({"sports": {"offseason_hide_after_days": 0}})
    assert skipped(t + 0.2)["mlb.team_summary"] == "off-season"

    snapshots.publish("mlb.season", {"phase": "preseason", "ended": None})                # spring training: back
    assert skipped(t + 0.3)["mlb.team_summary"] is None
    d.set_override("mlb.team_summary")                                                    # a preview is never blocked
    snapshots.publish("mlb.season", {"phase": "offseason"})
    d.frame(t + 0.4)
    assert d.active_board == "mlb.team_summary" and skipped(t + 0.4)["mlb.team_summary"] == "off-season"


def test_which_bundled_boards_follow_the_season():
    from scoreboard.plugins import load_registry
    boards = load_registry().boards
    seasonal = {k for k, b in boards.items() if b.seasonal}
    assert seasonal == ({f"{s}.team_summary" for s in ("nhl", "nfl", "ncaaf", "mlb", "ncaah", "ahl")}
                        | {f"{s}.standings" for s in ("nhl", "nfl", "ncaaf", "mlb", "ahl")}
                        | {"nhl.game_stats", "nhl.goal_replay", "nhl.leaders", "nhl.team_leaders"})
    assert all(boards[k].playlistable for k in seasonal)
