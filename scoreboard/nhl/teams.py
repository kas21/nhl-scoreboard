"""Team branding (colours, names) and logo lookup."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path

from PIL import Image

RGB = tuple[int, int, int]
ASSETS = Path(__file__).parent.parent / "assets"
BRANDING_FILE = ASSETS / "teams_branding.toml"
LOGO_DIR = ASSETS / "logos" / "png"

TEAM_NAMES: dict[str, tuple[str, str]] = {   # abbrev -> (city, nickname)
    "ANA": ("Anaheim", "Ducks"), "BOS": ("Boston", "Bruins"), "BUF": ("Buffalo", "Sabres"),
    "CAR": ("Carolina", "Hurricanes"), "CBJ": ("Columbus", "Blue Jackets"), "CGY": ("Calgary", "Flames"),
    "CHI": ("Chicago", "Blackhawks"), "COL": ("Colorado", "Avalanche"), "DAL": ("Dallas", "Stars"),
    "DET": ("Detroit", "Red Wings"), "EDM": ("Edmonton", "Oilers"), "FLA": ("Florida", "Panthers"),
    "LAK": ("Los Angeles", "Kings"), "MIN": ("Minnesota", "Wild"), "MTL": ("Montréal", "Canadiens"),
    "NJD": ("New Jersey", "Devils"), "NSH": ("Nashville", "Predators"), "NYI": ("New York", "Islanders"),
    "NYR": ("New York", "Rangers"), "OTT": ("Ottawa", "Senators"), "PHI": ("Philadelphia", "Flyers"),
    "PIT": ("Pittsburgh", "Penguins"), "SEA": ("Seattle", "Kraken"), "SJS": ("San Jose", "Sharks"),
    "STL": ("St. Louis", "Blues"), "TBL": ("Tampa Bay", "Lightning"), "TOR": ("Toronto", "Maple Leafs"),
    "UTA": ("Utah", "Mammoth"), "VAN": ("Vancouver", "Canucks"), "VGK": ("Vegas", "Golden Knights"),
    "WPG": ("Winnipeg", "Jets"), "WSH": ("Washington", "Capitals"),
}
NHL_TEAMS: tuple[str, ...] = tuple(sorted(TEAM_NAMES))


@dataclass(frozen=True)
class Team:
    abbrev: str
    city: str
    name: str
    primary: RGB
    accent: RGB
    text_on_primary: RGB
    text_on_accent: RGB

    @property
    def full_name(self) -> str:
        return f"{self.city} {self.name}"


DEFAULT_COLORS = {"primary": (90, 90, 90), "accent": (200, 200, 200), "text_on_primary": (255, 255, 255), "text_on_accent": (0, 0, 0)}


@lru_cache(maxsize=1)
def _branding() -> dict[str, dict]:
    try:
        raw = tomllib.loads(BRANDING_FILE.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict) and "primary" in v}


@cache
def team(abbrev: str) -> Team:
    """Branding for any abbrev; unknown teams get neutral colours, never an error."""
    abbrev = abbrev.upper()
    b = _branding().get(abbrev, {})
    city, name = TEAM_NAMES.get(abbrev, ("", abbrev))
    colors = {k: tuple(b.get(k, DEFAULT_COLORS[k])) for k in DEFAULT_COLORS}
    return Team(abbrev, city, name, **colors)  # type: ignore[arg-type]


@lru_cache(maxsize=256)
def logo(abbrev: str, size: int) -> Image.Image:
    """Pre-rasterised logo scaled to fit a ``size`` square (RGBA). Placeholder if missing."""
    candidates = sorted(LOGO_DIR.glob(f"{abbrev.upper()}_*.png"), key=lambda p: int(p.stem.split("_")[1]))
    src = next((p for p in candidates if int(p.stem.split("_")[1]) >= size), candidates[-1] if candidates else None)
    if src is None:
        return _placeholder(abbrev, size)
    img = Image.open(src).convert("RGBA")
    img.thumbnail((size, size), Image.LANCZOS)
    return img


def _placeholder(abbrev: str, size: int) -> Image.Image:
    from PIL import ImageDraw

    t = team(abbrev)
    img = Image.new("RGBA", (size, size), (*t.primary, 255))
    ImageDraw.Draw(img).rectangle((0, 0, size - 1, size - 1), outline=(*t.accent, 255))
    return img
