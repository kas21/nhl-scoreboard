from dataclasses import replace

from scoreboard.boards.clock import ClockBoard, ClockConfig
from scoreboard.boards.splash import SplashBoard, SplashConfig
from scoreboard.render.profiles import profile_for


def test_clock_renders_at_every_profile(ctx):
    board = ClockBoard()
    for w, h in [(64, 32), (64, 64), (128, 64), (128, 128), (256, 256)]:
        c = replace(ctx, width=w, height=h, profile=profile_for(w, h))
        frame = board.render(c, ClockConfig(show_seconds=True))
        assert frame.size == (w, h)
        assert frame.getbbox() is not None      # something drawn


def test_splash_animates(ctx):
    board = SplashBoard()
    cfg = SplashConfig()
    first = board.render(replace(ctx, elapsed=0.0), cfg)
    last = board.render(replace(ctx, elapsed=5.0), cfg)
    assert first.size == last.size == (128, 64)
    assert first.tobytes() != last.tobytes()


def test_profile_fallback():
    assert profile_for(128, 64).name == "128x64"
    assert profile_for(96, 48).name == "64x32"
    assert profile_for(8, 8).name == "64x32"


def test_clock_drops_rows_that_do_not_fit(ctx):
    small = replace(ctx, width=64, height=32, profile=profile_for(64, 32))
    frame = ClockBoard().render(small, ClockConfig(show_date=True))
    # rows that don't fit are dropped, so drawn content never touches the last row
    assert frame.crop((0, 31, 64, 32)).getbbox() is None
