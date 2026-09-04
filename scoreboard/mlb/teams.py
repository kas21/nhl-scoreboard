"""MLB team registry: static ids, divisions, colours and names; logos from the runtime cache.

The Stats API identifies clubs by numeric id and its abbreviations drift (OAK became ATH, ARI
became AZ), so everything keys off the id and the table here is the one source of truth for
the three-letter codes the boards show. Colours are the sister project's
(MLB-LED-Scoreboard ``colors/teams.example.json``): home, text-on-home, accent.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cache, lru_cache

from PIL import Image, ImageDraw

from ..logos import logo as cached_logo

RGB = tuple[int, int, int]

# id: (abbrev, city, nickname, division)
_TEAMS: dict[int, tuple[str, str, str, str]] = {
    110: ("BAL", "Baltimore", "Orioles", "AL East"),
    111: ("BOS", "Boston", "Red Sox", "AL East"),
    147: ("NYY", "New York", "Yankees", "AL East"),
    139: ("TB", "Tampa Bay", "Rays", "AL East"),
    141: ("TOR", "Toronto", "Blue Jays", "AL East"),
    145: ("CWS", "Chicago", "White Sox", "AL Central"),
    114: ("CLE", "Cleveland", "Guardians", "AL Central"),
    116: ("DET", "Detroit", "Tigers", "AL Central"),
    118: ("KC", "Kansas City", "Royals", "AL Central"),
    142: ("MIN", "Minnesota", "Twins", "AL Central"),
    117: ("HOU", "Houston", "Astros", "AL West"),
    108: ("LAA", "Los Angeles", "Angels", "AL West"),
    133: ("ATH", "", "Athletics", "AL West"),
    136: ("SEA", "Seattle", "Mariners", "AL West"),
    140: ("TEX", "Texas", "Rangers", "AL West"),
    144: ("ATL", "Atlanta", "Braves", "NL East"),
    146: ("MIA", "Miami", "Marlins", "NL East"),
    121: ("NYM", "New York", "Mets", "NL East"),
    143: ("PHI", "Philadelphia", "Phillies", "NL East"),
    120: ("WSH", "Washington", "Nationals", "NL East"),
    112: ("CHC", "Chicago", "Cubs", "NL Central"),
    113: ("CIN", "Cincinnati", "Reds", "NL Central"),
    158: ("MIL", "Milwaukee", "Brewers", "NL Central"),
    134: ("PIT", "Pittsburgh", "Pirates", "NL Central"),
    138: ("STL", "St. Louis", "Cardinals", "NL Central"),
    109: ("AZ", "Arizona", "Diamondbacks", "NL West"),
    115: ("COL", "Colorado", "Rockies", "NL West"),
    119: ("LAD", "Los Angeles", "Dodgers", "NL West"),
    135: ("SD", "San Diego", "Padres", "NL West"),
    137: ("SF", "San Francisco", "Giants", "NL West"),
}
DIVISION_ORDER = ("AL East", "AL Central", "AL West", "NL East", "NL Central", "NL West")
DIVISIONS: dict[str, list[str]] = {d: sorted(t[0] for t in _TEAMS.values() if t[3] == d) for d in DIVISION_ORDER}
DIVISION_OF = {t: d for d, ts in DIVISIONS.items() for t in ts}
LEAGUE_OF = {t: d.split()[0] for t, d in DIVISION_OF.items()}          # AL / NL
TEAM_IDS = {v[0]: k for k, v in _TEAMS.items()}                       # abbrev -> statsapi id
ABBREV_BY_ID = {k: v[0] for k, v in _TEAMS.items()}
MLB_TEAMS: tuple[str, ...] = tuple(sorted(TEAM_IDS))
# Stats API abbreviations that differ from ours (older feeds and the odd endpoint)
ALIASES = {"OAK": "ATH", "ARI": "AZ", "CHW": "CWS", "WAS": "WSH", "KCR": "KC", "SDP": "SD", "SFG": "SF", "TBR": "TB"}

# abbrev -> (primary, text on primary, accent)
_COLORS: dict[str, tuple[RGB, RGB, RGB]] = {
    "AZ": ((166, 25, 46), (217, 200, 157), (0, 0, 0)), "ATL": ((12, 35, 64), (255, 255, 255), (186, 12, 47)),
    "BAL": ((252, 76, 2), (0, 0, 0), (255, 255, 255)), "BOS": ((200, 16, 46), (255, 255, 255), (12, 35, 64)),
    "CHC": ((0, 47, 108), (255, 255, 255), (200, 16, 46)), "CWS": ((0, 0, 0), (141, 144, 147), (255, 255, 255)),
    "CIN": ((186, 12, 47), (255, 255, 255), (0, 0, 0)), "CLE": ((227, 227, 239), (204, 0, 46), (11, 34, 63)),
    "COL": ((51, 0, 114), (141, 144, 147), (0, 0, 0)), "DET": ((12, 35, 64), (250, 70, 22), (250, 70, 22)),
    "HOU": ((4, 30, 66), (207, 69, 32), (229, 114, 0)), "KC": ((0, 45, 114), (255, 255, 255), (137, 115, 76)),
    "LAA": ((186, 12, 47), (255, 255, 255), (12, 35, 64)), "LAD": ((0, 47, 108), (255, 255, 255), (145, 157, 157)),
    "MIA": ((0, 0, 0), (0, 163, 224), (239, 51, 64)), "MIL": ((19, 41, 75), (255, 199, 44), (0, 61, 165)),
    "MIN": ((12, 35, 64), (255, 255, 255), (186, 12, 47)), "NYM": ((0, 45, 114), (252, 76, 2), (255, 255, 255)),
    "NYY": ((12, 35, 64), (255, 255, 255), (255, 255, 255)), "ATH": ((2, 70, 56), (255, 184, 28), (255, 184, 28)),
    "PHI": ((186, 12, 47), (255, 255, 255), (0, 45, 114)), "PIT": ((0, 0, 0), (255, 199, 44), (255, 199, 44)),
    "SD": ((62, 52, 47), (255, 199, 44), (183, 169, 154)), "SF": ((250, 70, 22), (0, 0, 0), (239, 209, 159)),
    "SEA": ((12, 44, 86), (141, 144, 147), (0, 104, 94)), "STL": ((186, 12, 47), (255, 255, 255), (12, 35, 64)),
    "TB": ((4, 30, 66), (255, 255, 255), (105, 179, 231)), "TEX": ((0, 45, 114), (255, 255, 255), (186, 12, 47)),
    "TOR": ((0, 61, 165), (255, 255, 255), (108, 172, 228)), "WSH": ((186, 12, 47), (255, 255, 255), (4, 30, 66)),
}
DEFAULT_COLORS: tuple[RGB, RGB, RGB] = ((90, 90, 90), (255, 255, 255), (200, 200, 200))


@dataclass(frozen=True)
class Team:
    abbrev: str
    city: str
    name: str
    primary: RGB
    accent: RGB
    text_on_primary: RGB
    division: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.city} {self.name}".strip()


def canonical(abbrev: str | None) -> str:
    """Our code for any abbreviation the API might hand us (``OAK`` -> ``ATH``)."""
    a = (abbrev or "").upper()
    return ALIASES.get(a, a)


def abbrev_for(team_id: int | None, fallback: str | None = None) -> str:
    return ABBREV_BY_ID.get(int(team_id or 0), canonical(fallback))


@cache
def team(abbrev: str) -> Team:
    """Branding for any abbrev; unknown teams get neutral colours, never an error."""
    abbrev = canonical(abbrev)
    primary, text, accent = _COLORS.get(abbrev, DEFAULT_COLORS)
    info = _TEAMS.get(TEAM_IDS.get(abbrev, 0))
    city, name, division = (info[1], info[2], info[3]) if info else ("", abbrev, "")
    return Team(abbrev, city, name, primary, accent, text, division)


def colors(abbrev: str) -> tuple[RGB, RGB]:
    t = team(abbrev)
    return t.primary, t.accent


def text_on(abbrev: str) -> RGB:
    return team(abbrev).text_on_primary


def logo(abbrev: str, size: int) -> Image.Image:
    """Logo scaled to fit a ``size`` square (RGBA), or a neutral tile until the fetch lands."""
    img = cached_logo("mlb", canonical(abbrev), size)
    return img if img is not None else _placeholder(canonical(abbrev), size)


@lru_cache(maxsize=128)
def _placeholder(abbrev: str, size: int) -> Image.Image:
    t = team(abbrev)
    img = Image.new("RGBA", (size, size), (*t.primary, 255))
    ImageDraw.Draw(img).rectangle((0, 0, size - 1, size - 1), outline=(*t.accent, 255))
    return img
