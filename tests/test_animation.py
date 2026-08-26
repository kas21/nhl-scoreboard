import time

from PIL import Image

from scoreboard.render import (
    Blink,
    Box,
    Fade,
    HBox,
    Img,
    Marquee,
    Pulse,
    Sequence,
    Sheen,
    Slide,
    Spacer,
    Text,
    VBox,
    load_font,
    render_tree,
)
from scoreboard.render.layout import _cache, clear_cache


def solid(w, h, color=(255, 0, 0, 255)):
    return Image.new("RGBA", (w, h), color)


def frames(node, w, h, ts):
    return [render_tree(node, w, h, t=t).tobytes() for t in ts]


def test_static_subtree_is_cached_and_reused():
    clear_cache()
    tree = HBox([Img(solid(4, 4)), Spacer(), Text("A", load_font("pixel", 8))])
    render_tree(tree, 32, 8)
    n = len(_cache)
    assert tree.is_static and n >= 1
    render_tree(tree, 32, 8, t=5.0)
    assert len(_cache) == n                     # second render hit the cache, added nothing


def test_animated_child_makes_container_dynamic():
    tree = VBox([Text("x", load_font("pixel", 8)), Blink(Text("y", load_font("pixel", 8)))])
    assert not tree.is_static and tree.cache_key() is None


def test_marquee_scrolls_only_when_too_wide():
    font = load_font("pixel", 8)
    narrow = Marquee(Text("HI", font), width=40, speed=20)
    assert len(set(frames(narrow, 40, 8, [0, 1, 2, 3]))) == 1
    wide = Marquee(Text("A VERY LONG TEAM NAME", font), width=40, speed=20, pause=0)
    assert wide.measure()[0] == 40
    assert len(set(frames(wide, 40, 8, [0, 0.5, 1.0]))) == 3


def test_sheen_pulse_blink_vary_with_time_and_keep_size():
    font = load_font("pixel", 8)
    badge = HBox([Box(20, 10, (255, 200, 0, 255)), Text("PP", font, (0, 0, 0))])
    for node in (Sheen(badge, period=1.0), Pulse(badge, period=1.0), Blink(badge, period=1.0)):
        fs = frames(node, 32, 12, [0.0, 0.25, 0.6])
        assert len(set(fs)) >= 2, type(node).__name__
        assert node.measure() == badge.measure()


def test_slide_and_fade_settle():
    font = load_font("pixel", 8)
    node = Slide(Text("GO", font), duration=0.5, direction="left")
    start, end, later = frames(node, 32, 8, [0.0, 0.5, 9.0])
    assert start != end and end == later
    fade = Fade(Text("GO", font), duration=0.5, start=0.0, end=1.0)
    f0, f1 = frames(fade, 32, 8, [0.0, 1.0])
    assert f0 != f1 and f1 == frames(Text("GO", font), 32, 8, [0])[0]


def test_sequence_durations_and_playback():
    still = Image.new("RGB", (16, 8), (0, 255, 0))
    seq = Sequence(30).flash((255, 0, 0), times=2, secs=0.4).slide_in("right", 0.5).hold(2).fade_out(0.5).build(still)
    assert abs(seq.duration - 3.4) < 0.15
    assert seq.at(0).getpixel((0, 0)) == (255, 0, 0)          # flash starts on colour
    assert seq.at(1.5).getpixel((0, 0)) == (0, 255, 0)        # holding
    assert seq.at(99).getpixel((0, 0)) == (0, 0, 0)           # faded out, clamped
    assert seq.finished(3.5) and not seq.finished(1.0)


def test_live_board_frame_budget_with_animated_badges():
    """Static parts cached; per-frame cost stays small even with sheen + pulse."""
    import json
    from pathlib import Path

    from scoreboard.data import SnapshotStore
    from scoreboard.nhl.boards.game import GameBoard, GameConfig
    from scoreboard.nhl.normalize import normalize_game
    from tests.test_nhl_boards import make_ctx, world  # noqa: F401  (fixture factory)
    score = json.loads((Path(__file__).parent / "fixtures/nhl/score_2026-04-11.json").read_text())
    g = normalize_game(next(x for x in score["games"] if x["homeTeam"]["abbrev"] == "TOR"))
    live = {**g, "state": "LIVE", "phase": "live", "clock": "12:34", "period": "2nd", "outcome": "",
            "powerplay": {"code": "h54", "clock": "01:12"}, "pulled_goalie": 1}
    w = {"store": SnapshotStore()}
    board = GameBoard()
    board.render(make_ctx(w, 128, 64, live), GameConfig())          # warm caches
    t0 = time.perf_counter()
    n = 60
    for i in range(n):
        board.render(make_ctx(w, 128, 64, live, elapsed=i / 30), GameConfig())
    per_frame_ms = (time.perf_counter() - t0) / n * 1000
    assert per_frame_ms < 8, per_frame_ms


def test_animated_child_inside_slide_keeps_animating():
    """A Sheen wrapped in a Slide must advance with t (regression: it was frozen at t=0)."""
    from PIL import Image as _I
    badge = Img(_I.new("RGBA", (30, 10), (200, 200, 200, 255)))
    node = Slide(Sheen(badge, period=1.0, band=6, strength=1.0), duration=0.2, direction="left")
    fs = frames(node, 40, 12, [0.5, 0.7, 0.9])
    assert len(set(fs)) == 3
