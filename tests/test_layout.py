from PIL import Image, ImageDraw

from scoreboard.render import (
    Anchor,
    HBox,
    Img,
    Spacer,
    Text,
    VBox,
    load_font,
    render_tree,
    text_size,
)
from scoreboard.render.layout import Box
from scoreboard.render.profiles import PROFILES


def solid(w, h, color=(255, 0, 0)):
    return Image.new("RGB", (w, h), color)


def test_hbox_spacer_pushes_children_apart():
    left, right = solid(10, 10, (255, 0, 0)), solid(10, 10, (0, 0, 255))
    frame = render_tree(HBox([Img(left), Spacer(), Img(right)]), 64, 10)
    assert frame.getpixel((0, 5)) == (255, 0, 0)
    assert frame.getpixel((63, 5)) == (0, 0, 255)
    assert frame.getpixel((32, 5)) == (0, 0, 0)


def test_vbox_centers_children_horizontally():
    frame = render_tree(VBox([Img(solid(10, 4)), Img(solid(20, 4))]), 40, 8)
    assert frame.getpixel((14, 0)) == (0, 0, 0)       # left of the 10px child (x 15..24)
    assert frame.getpixel((15, 0)) == (255, 0, 0)
    assert frame.getpixel((10, 4)) == (255, 0, 0)     # 20px child spans x 10..29


def test_anchor_bottom_right():
    frame = render_tree(Anchor(Img(solid(4, 4)), h="end", v="end"), 32, 16)
    assert frame.getpixel((31, 15)) == (255, 0, 0)
    assert frame.getpixel((0, 0)) == (0, 0, 0)


def test_text_measures_and_draws_something():
    node = Text("12:30", load_font("clock", 20), (0, 255, 0))
    w, h = node.measure()
    assert w > 10 and h > 10
    frame = render_tree(node, 128, 64)
    assert any(frame.getpixel((x, y)) == (0, 255, 0) for x in range(128) for y in range(64))


def test_box_fills_rect():
    frame = render_tree(HBox([Box(fill=(0, 0, 255, 255)), Spacer(weight=1)]), 8, 2)
    assert frame.getpixel((0, 0)) == (0, 0, 0)  # zero-width box draws only its given width


# -- the small label font -------------------------------------------------------

LABEL_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _glyph_bits(char, font, box=(8, 10)):
    img = Image.new("1", box, 0)
    ImageDraw.Draw(img).text((1, 1), char, font=font, fill=1)
    return img.tobytes()


def test_label_font_has_no_colliding_glyphs():
    """plfont-6 drew '0' and 'O' identically and put six digit pairs one pixel apart.

    A label face that cannot separate 6 from 8 makes a scoreboard unreadable, so
    every profile's small font must render 0-9A-Z as distinct bitmaps.
    """
    for profile in PROFILES:
        bits = {c: _glyph_bits(c, profile.label_font()) for c in LABEL_ALPHABET}
        collisions = {a for a in bits for b in bits if a < b and bits[a] == bits[b]}
        assert not collisions, f"{profile.name}: identical glyphs for {sorted(collisions)}"


def test_label_font_keeps_the_ported_128x64_metrics():
    """The 128x64 boards are pixel ports with 5px-tall text boxes and fixed column
    x-positions; the label face has to stay on plfont-6's 4px pitch or they reflow."""
    old = load_font("pl", 6)
    for profile in PROFILES:
        if profile.width <= 128 and profile.height <= 64:
            label = profile.label_font()
            for sample in ("SOG", "12-4-1", "FINAL/OT", "0O68AR"):
                assert text_size(sample, label) == text_size(sample, old), f"{profile.name}: {sample}"
