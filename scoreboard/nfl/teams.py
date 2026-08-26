"""NFL team registry: static divisions, logos from assets, colours learned from the API."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

LOGO_DIR = Path(__file__).parent.parent / "assets" / "logos" / "nfl"
DIVISIONS = {
    "AFC East": ["BUF", "MIA", "NE", "NYJ"], "AFC North": ["BAL", "CIN", "CLE", "PIT"],
    "AFC South": ["HOU", "IND", "JAX", "TEN"], "AFC West": ["DEN", "KC", "LAC", "LV"],
    "NFC East": ["DAL", "NYG", "PHI", "WSH"], "NFC North": ["CHI", "DET", "GB", "MIN"],
    "NFC South": ["ATL", "CAR", "NO", "TB"], "NFC West": ["ARI", "LAR", "SEA", "SF"],
}
DIVISION_OF = {t: d for d, ts in DIVISIONS.items() for t in ts}
NFL_TEAMS = tuple(sorted(DIVISION_OF))
RGB = tuple[int, int, int]
_colors: dict[str, tuple[RGB, RGB]] = {}     # abbrev -> (primary, alternate), filled by the source


def hex_rgb(h: str | None, default: RGB = (90, 90, 90)) -> RGB:
    try:
        h = (h or "").lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except (ValueError, IndexError):
        return default


def learn_colors(abbrev: str, primary: str | None, alternate: str | None) -> None:
    _colors[abbrev.upper()] = (hex_rgb(primary), hex_rgb(alternate, (255, 255, 255)))


def colors(abbrev: str) -> tuple[RGB, RGB]:
    return _colors.get(abbrev.upper(), ((90, 90, 90), (255, 255, 255)))


def text_on(bg: RGB) -> RGB:
    lum = 0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]
    return (0, 0, 0) if lum > 140 else (255, 255, 255)


@lru_cache(maxsize=128)
def logo(abbrev: str, size: int) -> Image.Image:
    path = LOGO_DIR / f"{abbrev.upper()}_128.png"
    if not path.exists():
        img = Image.new("RGBA", (size, size), (*colors(abbrev)[0], 255))
        ImageDraw.Draw(img).rectangle((0, 0, size - 1, size - 1), outline=(255, 255, 255, 255))
        return img
    img = Image.open(path).convert("RGBA")
    img.thumbnail((size, size), Image.LANCZOS)
    return img
