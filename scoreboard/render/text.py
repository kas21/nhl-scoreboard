"""Font loading (cached) and text measuring."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONT_DIR = Path(__file__).parent / "fonts"

FONTS = {
    "pixel": "CutePixel.ttf",
    "score": "score_font.ttf",
    "clock": "clock_font.ttf",
    "block": "minecraft_bold.ttf",
}
DEFAULT_FONT = "pixel"


@lru_cache(maxsize=128)
def load_font(name: str = DEFAULT_FONT, size: int = 8) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a bundled font by short name (or a path). Falls back to PIL's default."""
    path = FONT_DIR / FONTS.get(name, name)
    if path.suffix.lower() == ".bdf":
        return ImageFont.load(str(path.with_suffix(".pil"))) if path.with_suffix(".pil").exists() else ImageFont.load_default()
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
    mode = "L" if antialias else "1"
    try:
        return font.getbbox(text, mode=mode, anchor="la")
    except TypeError:                   # bitmap fonts: no mode/anchor kwargs
        return font.getbbox(text)


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
