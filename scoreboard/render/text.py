"""Font loading (cached) and text measuring."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONT_DIR = Path(__file__).parent / "fonts"

FONTS = {                       # vector fonts, sized freely (large text only)
    "score": "score_font.ttf",
    "clock": "clock_font.ttf",
    "block": "minecraft_bold.ttf",
    "camels": "mutant_camels.ttf",
    "ari": "ari_w9500.ttf",
    "gothic": "special_gothic.ttf",
    "upheaval": "upheaval.ttf",
}
# Hand-drawn bitmap fonts (public-domain X11 set) keyed by pixel height. These
# are what make small text legible on an LED matrix; TrueType rasterised at
# 6-10 px turns to mush.
BITMAP = {
    "pixel": {6: "tom-thumb", 7: "5x7", 8: "5x8", 9: "6x9", 10: "6x10", 12: "6x12", 13: "7x13", 15: "9x15B", 18: "9x18B", 20: "10x20"},
    "pixelbold": {6: "tom-thumb", 7: "5x7", 8: "5x8", 9: "6x9", 10: "6x10", 12: "6x12", 13: "6x13B", 14: "7x13B", 15: "8x13B", 18: "9x18B", 20: "10x20"},
    "pl": {6: "plfont-6", 12: "plfont-12"},          # the old client's default UI font (4px pitch, 5 tall)
    "narrow": {6: "4x6", 7: "4x6", 8: "5x8", 9: "6x9", 10: "6x10", 12: "6x12", 13: "7x13", 15: "9x15B", 18: "9x18B", 20: "10x20"},
}
DEFAULT_FONT = "pixel"


def is_bitmap(font: ImageFont.ImageFont) -> bool:
    return not isinstance(font, ImageFont.FreeTypeFont)


@lru_cache(maxsize=128)
def load_font(name: str = DEFAULT_FONT, size: int = 8) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a bundled font by family name (or a TTF path) at ``size`` px.

    Bitmap families snap to the largest bundled face that is <= size.
    """
    family = BITMAP.get(name)
    if family:
        best = max((h for h in family if h <= size), default=min(family))
        return ImageFont.load(str(FONT_DIR / "pil" / f"{family[best]}.pil"))
    path = FONT_DIR / FONTS.get(name, name)
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default()


def text_box(text: str, font: ImageFont.ImageFont, antialias: bool = False) -> tuple[int, int, int, int]:
    """Glyph box (l, t, r, b) for ``text`` drawn at (0, 0) with the "la" anchor.

    Measured in the same render mode as drawing: 1-bit mode disables hinting
    and changes advances, so measuring antialiased would clip. Uses the glyph
    box rather than font metrics because pixel fonts often declare descenders
    far larger than they draw.
    """
    if is_bitmap(font):
        left, top, right, bottom = font.getbbox(text)
        return left, top, right, bottom
    mode = "L" if antialias else "1"
    return font.getbbox(text, mode=mode, anchor="la")


def text_size(text: str, font: ImageFont.ImageFont, antialias: bool = False) -> tuple[int, int]:
    """Tight (width, height) of the ink for ``text``."""
    left, top, right, bottom = text_box(text, font, antialias)
    return int(right - left), int(bottom - top)


def fit_font(text: str, name: str, max_width: int, start: int, minimum: int = 5):
    """Largest size of ``name`` at which ``text`` fits within ``max_width``."""
    for size in range(start, minimum - 1, -1):
        font = load_font(name, size)
        if text_size(text, font)[0] <= max_width:
            return font
    return load_font(name, minimum)
