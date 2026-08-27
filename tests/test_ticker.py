"""Ticker mode: the playlist laid out as a strip and scrolled through the panel."""
from datetime import UTC, datetime

from PIL import Image

from scoreboard.boards.base import BaseBoard, BoardContext, EmptyConfig, EventBoard
from scoreboard.boards.blank import BlankBoard
from scoreboard.boards.clock import ClockBoard
from scoreboard.boards.splash import SplashBoard
from scoreboard.config import ConfigStore
from scoreboard.data import Snapshot, SnapshotStore
from scoreboard.data.events import Event, EventBus
from scoreboard.director import AppState, Director
from scoreboard.director.director import BOOT_SECONDS
from scoreboard.director.strip import MAX_TILES, Strip, StripFrame
from scoreboard.plugins import Registry
from scoreboard.render.profiles import profile_for

NOW = datetime(2026, 1, 15, 19, 5, tzinfo=UTC)
BLUE, GREEN, WHITE = (0, 0, 255), (0, 255, 0), (255, 255, 255)


class ColorBoard(BaseBoard):
    """Fills its tile with one colour; ``enter`` counts, so per-tile instancing is observable."""

    color = BLUE

    def __init__(self) -> None:
        self.entries = 0

    def enter(self, ctx, cfg):
        self.entries += 1

    def render(self, ctx, cfg):
        return Image.new("RGB", (ctx.width, ctx.height), self.color)


class BlueBoard(ColorBoard):
    key, title, color = "blue", "Blue", BLUE


class GreenBoard(ColorBoard):
    key, title, color = "green", "Green", GREEN


class ClockFaceBoard(ColorBoard):
    """Paints its own ``ctx.elapsed`` so each tile's clock is readable off the frame."""

    key, title = "elapsed", "Elapsed"

    def render(self, ctx, cfg):
        level = min(int(ctx.elapsed * 10), 255)
        return Image.new("RGB", (ctx.width, ctx.height), (level, level, level))


class BrokenBoard(ColorBoard):
    key, title = "broken", "Broken"

    def render(self, ctx, cfg):
        raise RuntimeError("boom")


class BrokenEntryBoard(ColorBoard):
    key, title = "broken_entry", "Broken on entry"

    def enter(self, ctx, cfg):
        raise RuntimeError("boom")


CLASSES = {c.key: c for c in (BlueBoard, GreenBoard, ClockFaceBoard, BrokenBoard, BrokenEntryBoard)}


class Harness:
    """Drives a Strip the way the director does, recording what it built and what failed."""

    def __init__(self, **defaults):
        self.strip = Strip()
        self.built: list[ColorBoard] = []
        self.errors: list[str] = []
        self.defaults = {"keys": ("blue", "green"), "width": 64, "height": 32,
                         "tile_width": 0, "speed": 32.0, "gap": 0, **defaults}

    def at(self, mono: float, **overrides) -> Image.Image:
        opts = {**self.defaults, **overrides}

        def make_board(key):
            cls = CLASSES.get(key)
            if cls is None:
                return None
            board = cls()
            self.built.append(board)
            return board

        def make_ctx(entered, w, h):
            return BoardContext(snapshot=Snapshot(), profile=profile_for(w, h), width=w, height=h,
                                fps=30, now=NOW, elapsed=mono - entered, ticker=True)

        return self.strip.frame(StripFrame(
            mono=mono, make_board=make_board, make_ctx=make_ctx,
            board_cfg=lambda b: EmptyConfig(), on_error=self.errors.append, **opts))


FPS = 30


def row(img):
    """The top row as a list of pixels — enough to see where each tile sits."""
    return [img.getpixel((x, 0)) for x in range(img.width)]


def run(draw, start: float, seconds: float, **overrides):
    """Drive something frame by frame at 30 fps, the way the render loop does. Returns the last frame."""
    frame = None
    for i in range(int(round(seconds * FPS)) + 1):
        frame = draw(start + i / FPS, **overrides)
    return frame


# -- the strip itself --------------------------------------------------------


def test_strip_scrolls_boards_through_the_viewport():
    h = Harness()
    assert set(row(h.at(0.0))) == {BLUE}                # first tile fills the panel, the next waits off-screen
    half = run(h.at, 0.0, 1.0)                          # 32 px/s for 1 s across a 64 px panel
    assert half.getpixel((0, 0)) == BLUE and half.getpixel((63, 0)) == GREEN
    assert abs(row(half).index(GREEN) - 32) <= 1
    assert set(row(run(h.at, 1.0, 1.0))) == {GREEN}     # blue has left entirely


def test_strip_wraps_the_playlist_and_keeps_coordinates_bounded():
    h = Harness()
    frame = run(h.at, 0.0, 100.0)                       # ~50 tiles through the panel
    assert h.strip._offset < h.defaults["width"] * 2    # rebased on every prune, not growing without bound
    assert len(h.strip._tiles) <= MAX_TILES
    assert set(row(frame)) <= {BLUE, GREEN}


def test_each_tile_keeps_its_own_clock():
    h = Harness(keys=("elapsed",))
    frame = run(h.at, 0.0, 3.0)                         # the tiles now on screen were built at different times
    tones = {px[0] for px in row(frame)}
    assert len(tones) == 2
    assert max(tones) > min(tones)


def test_repeated_board_gets_its_own_instance_per_tile():
    h = Harness(keys=("blue", "blue"))
    h.at(0.0)
    assert len(h.built) == 2
    assert h.built[0] is not h.built[1]
    assert all(b.entries == 1 for b in h.built)         # entered once each, not twice on one shared board


def test_board_that_raises_is_reported_and_dropped():
    h = Harness(keys=("broken", "green"))
    frame = h.at(0.0)
    assert h.errors == ["broken"]                       # quarantined by the director, not fatal here
    assert frame.size == (64, 32)
    assert run(h.at, 0.0, 1.0).size == (64, 32)


def test_board_that_raises_on_entry_is_skipped_without_a_tile():
    h = Harness(keys=("broken_entry", "green"))
    frame = h.at(0.0)
    assert h.errors == ["broken_entry"]                 # reported once, not retried all the way down the strip
    assert set(row(frame)) == {GREEN}                   # the strip closed over the gap


def test_a_playlist_that_cannot_build_yields_a_blank_frame():
    h = Harness(keys=("broken_entry",))
    frame = h.at(0.0)
    assert h.errors == ["broken_entry"] and frame.getbbox() is None


def test_narrow_tiles_put_several_boards_on_screen_at_once():
    h = Harness(tile_width=16)
    colors = row(h.at(0.0))
    assert colors[:16] == [BLUE] * 16 and colors[16:32] == [GREEN] * 16
    assert len(set(colors)) == 2


def test_gap_between_tiles_is_blank():
    h = Harness(tile_width=16, gap=4)
    colors = row(h.at(0.0))
    assert colors[:16] == [BLUE] * 16
    assert colors[16:20] == [(0, 0, 0)] * 4
    assert colors[20:36] == [GREEN] * 16


def test_geometry_change_rebuilds_the_strip():
    h = Harness()
    assert set(row(h.at(0.0))) == {BLUE}
    colors = row(h.at(1.0, tile_width=16))              # narrower tiles: start over rather than mix widths
    assert colors[:16] == [BLUE] * 16 and colors[16:32] == [GREEN] * 16


def test_playlist_change_keeps_what_is_on_screen():
    on_screen = run((h := Harness()).at, 0.0, 1.0)
    assert on_screen.getpixel((0, 0)) == BLUE
    after = h.at(1.0 + 1 / FPS, keys=("green",))        # queue rebuilt, the visible tiles are not yanked
    assert after.getpixel((0, 0)) == BLUE


def test_stalled_render_loop_does_not_teleport_the_strip():
    h = Harness()
    h.at(0.0)
    h.at(60.0)                                          # a minute-long stall clamps to one step of travel
    assert h.strip._offset <= h.defaults["speed"] * 0.25


# -- the director ------------------------------------------------------------


class GoalBoard(EventBoard):
    key, title = "goal", "Goal"
    event_kinds = frozenset({"goal"})

    def render(self, ctx, cfg):
        return Image.new("RGB", (ctx.width, ctx.height), WHITE)

    def done(self, ctx, cfg):
        return ctx.elapsed >= 2.0


def make(tmp_path, ticker: dict | None = None):
    config = ConfigStore(tmp_path / "config.json")
    config.update({
        "display": {"width": 64, "height": 32},
        "playlists": {"offday": [{"board": "blue", "duration": 5}, {"board": "green", "duration": 5}]},
        "ticker": {"enabled": True, "speed": 32.0, **(ticker or {})},
    })
    snapshots, events = SnapshotStore(), EventBus()
    snapshots.subscribe(events.on_snapshot)
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard(), BlankBoard(),
                                              BlueBoard(), GreenBoard(), BrokenBoard(), GoalBoard())})
    return config, snapshots, events, Director(config, snapshots, reg, events)


def booted(d, t0=1000.0):
    d.frame(t0)
    t = t0 + BOOT_SECONDS + 0.1
    d.frame(t)
    assert d.state == AppState.OFFDAY
    return t


def test_director_scrolls_the_playlist_and_names_the_centre_board(tmp_path):
    _, _, _, d = make(tmp_path)
    t = booted(d)
    assert d.active_board == "blue"
    frame = run(d.frame, t, 1.0)                        # 32 px/s across a 64 px panel: half and half
    assert frame.getpixel((0, 0)) == BLUE and frame.getpixel((63, 0)) == GREEN
    assert d.active_board == "green"                    # the board under the middle of the panel


def test_director_boots_normally_before_the_ticker_takes_over(tmp_path):
    _, _, _, d = make(tmp_path)
    d.frame(1000.0)
    assert d.state == AppState.BOOT and d.active_board == "splash"


def test_events_still_cut_in_over_the_ticker_and_it_resumes(tmp_path):
    _, _, events, d = make(tmp_path)
    t = booted(d)
    run(d.frame, t, 1.0)
    events._queue.append(Event("goal", team="TOR", ts=t))
    frame = d.frame(t + 1.1)
    assert d.active_board == "goal" and frame.getpixel((0, 0)) == WHITE
    d.frame(t + 3.2)                                    # goal board reports done
    resumed = run(d.frame, t + 3.3, 1.0)
    assert d.active_board in ("blue", "green")
    assert resumed.getpixel((0, 0)) != WHITE


def test_override_still_pins_a_board_over_the_ticker(tmp_path):
    _, _, _, d = make(tmp_path)
    t = booted(d)
    d.set_override("green", seconds=60)
    frame = run(d.frame, t, 1.0)                        # past the fade out of the strip
    assert d.active_board == "green" and set(row(frame)) == {GREEN}


def test_broken_board_is_quarantined_out_of_the_strip(tmp_path):
    config, _, _, d = make(tmp_path)
    config.update({"playlists": {"offday": [{"board": "broken", "duration": 5}, {"board": "green", "duration": 5}]}})
    t = booted(d)
    frame = run(d.frame, t, 4.0)
    assert set(row(frame)) == {GREEN}                   # only the healthy board is left in the strip


def test_ticker_disabled_leaves_the_slideshow_alone(tmp_path):
    config, _, _, d = make(tmp_path, ticker={"enabled": False})
    config.update({"transition": {"style": "none"}})
    t = booted(d)
    assert set(row(d.frame(t + 0.1))) == {BLUE}         # one board fills the panel
    d.frame(t + 5.2)
    d.frame(t + 5.3)
    assert d.active_board == "green"


def test_stale_marker_still_shows_over_the_ticker(tmp_path):
    _, snapshots, _, d = make(tmp_path)
    t = booted(d)
    snapshots.publish("nhl.scores", [])
    snapshots.publish("system", {"online": False})
    frame = d.frame(t + 0.1)
    assert frame.getpixel((frame.width - 2, frame.height - 2)) == (200, 40, 40)


def test_event_with_no_board_does_not_interrupt_the_ticker(tmp_path):
    _, _, events, d = make(tmp_path)
    t = booted(d)
    events._queue.append(Event("touchdown", team="TOR", ts=t))    # nothing matches it
    frame = d.frame(t + 0.1)
    assert d.active_board in ("blue", "green")                    # no one-frame flash of the slideshow
    assert frame.getpixel((0, 0)) == BLUE
