"""Text measuring: bitmap faces are measured by their ink, like TrueType faces."""
from __future__ import annotations

from scoreboard.render import Text, load_font, render_node
from scoreboard.render.fx import chip
from scoreboard.render.text import text_box, text_size


def test_bitmap_text_box_is_the_ink_not_the_cell():
    f = load_font("pixel", 6)                       # tom-thumb: 4x6 cell, 3x5 ink for caps and digits
    assert f.getbbox("7") == (0, 0, 4, 6)
    assert text_box("7", f) == (0, 0, 3, 5)
    assert text_box("SOG", f) == (0, 0, 11, 5)
    assert text_box("gy", f) == (0, 1, 7, 6)         # descenders reach the sixth row
    assert text_box(":", f) == (1, 1, 2, 4)          # inset glyphs report their offset


def test_bitmap_text_box_keeps_spaces_at_either_end():
    f = load_font("pixel", 6)
    assert text_box("A ", f) == (0, 0, 8, 5)         # one 4px cell of trailing space
    assert text_box(" A", f) == (0, 0, 7, 5)
    assert text_box("A B", f)[2] == 11                # inner spaces are already inside the ink box


def test_bitmap_text_box_falls_back_to_the_cell_without_ink():
    f = load_font("pixel", 6)
    assert text_box("", f) == f.getbbox("")
    assert text_box(" ", f) == f.getbbox(" ")


def test_bitmap_text_draws_its_full_ink_at_the_measured_size():
    f = load_font("pixel", 6)
    img = render_node(Text("7", f))
    assert img.size == text_size("7", f) == (3, 5)
    top = [img.getpixel((x, 0))[3] for x in range(3)]
    assert top == [255, 255, 255], "the top bar of the 7 must not be lost"


def test_chip_is_the_ink_plus_padding():
    f = load_font("pixel", 6)
    assert chip("SOG", f, (0, 0, 0), (255, 255, 255)).size == (13, 7)
