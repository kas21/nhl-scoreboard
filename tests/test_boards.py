from dataclasses import replace

from scoreboard.boards.clock import CLOCK_FONTS, ClockBoard, ClockConfig
from scoreboard.boards.splash import SplashBoard, SplashConfig
from scoreboard.render.profiles import profile_for


def test_clock_renders_at_every_profile(ctx):
    board = ClockBoard()
    for w, h in [(64, 32), (64, 64), (128, 64), (128, 128), (256, 256)]:
        c = replace(ctx, width=w, height=h, profile=profile_for(w, h))
        frame = board.render(c, ClockConfig())
        assert frame.size == (w, h)
        assert frame.getbbox() is not None      # something drawn


def test_splash_animates(ctx):
    board = SplashBoard()
    cfg = SplashConfig()
    first = board.render(replace(ctx, elapsed=0.0), cfg)
    last = board.render(replace(ctx, elapsed=5.0), cfg)
    assert first.size == last.size == (128, 64)
    assert first.tobytes() != last.tobytes()


def test_bitmap_fonts_snap_to_nearest_size():
    from scoreboard.render.text import is_bitmap, load_font, text_size
    f = load_font("pixel", 8)
    assert is_bitmap(f) and text_size("78 PTS", f)[1] == 8
    assert text_size("W", load_font("pixel", 11))[1] == 10       # 11 -> 6x10
    assert text_size("W", load_font("pixel", 3))[1] == 6         # below smallest -> tom-thumb


def test_profile_fallback():
    assert profile_for(128, 64).name == "128x64"
    assert profile_for(96, 48).name == "64x32"
    assert profile_for(8, 8).name == "64x32"


def test_clock_fills_the_panel(ctx):
    board = ClockBoard()
    widest = ctx.now.replace(hour=22, minute=47)
    for w, h in [(64, 32), (128, 32), (128, 64), (192, 128)]:
        c = replace(ctx, width=w, height=h, profile=profile_for(w, h), now=widest)
        left, top, right, bottom = board.render(c, ClockConfig()).getbbox()
        assert left >= 0 and top >= 0 and right <= w and bottom <= h         # nothing clipped
        # the stacked date/time/year block grows until one axis is full
        assert right - left >= w * 0.8 or bottom - top >= h * 0.8
    tall = replace(ctx, width=128, height=64, profile=profile_for(128, 64), now=widest)
    left, top, right, bottom = board.render(tall, ClockConfig()).getbbox()
    assert bottom - top >= 64 * 0.7


def test_clock_renders_in_every_font(ctx):
    board, widest = ClockBoard(), ctx.now.replace(hour=22, minute=47)
    for family in CLOCK_FONTS:
        left, top, right, bottom = board.render(replace(ctx, now=widest), ClockConfig(font=family)).getbbox()
        assert left >= 0 and top >= 0 and right <= ctx.width and bottom <= ctx.height


def test_clock_digits_keep_one_size_across_the_hour(ctx):
    """Sized for the widest time, so the digits don't grow and shrink each minute."""
    def height(hour, minute):
        box = ClockBoard().render(replace(ctx, now=ctx.now.replace(hour=hour, minute=minute)), ClockConfig()).getbbox()
        return box[3] - box[1]

    assert height(22, 47) == height(9, 5)


def test_clock_drops_rows_that_do_not_fit(ctx):
    small = replace(ctx, width=64, height=32, profile=profile_for(64, 32))
    frame = ClockBoard().render(small, ClockConfig(show_date=True))
    # rows that don't fit are dropped, so drawn content never touches the last row
    assert frame.crop((0, 31, 64, 32)).getbbox() is None
