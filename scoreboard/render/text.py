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


def text_size(text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    """Box needed to draw ``text`` at (0, 0) with the top-left ("la") anchor.

    Uses the glyph box rather than font metrics: pixel fonts often declare
    descenders far larger than they draw, which would waste LED rows.
    """
    _, _, right, bottom = font.getbbox(text, anchor="la")
    advance = font.getlength(text) if hasattr(font, "getlength") else right
    return int(max(right, advance)) + 1, int(bottom)


def fit_font(text: str, name: str, max_width: int, start: int, minimum: int = 5):
    """Largest size of ``name`` at which ``text`` fits within ``max_width``."""
    for size in range(start, minimum - 1, -1):
        font = load_font(name, size)
        if text_size(text, font)[0] <= max_width:
            return font
    return load_font(name, minimum)
