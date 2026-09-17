"""AHL registry: the 32 clubs of the 2026-27 season with their NHL parents, logos from the
runtime cache (HockeyTech hosts them; the source registers the URLs), colours from the parent
club's NHL branding unless the affiliate wears its own.

Codes are HockeyTech's team codes (they key the games, the standings and the favourites
picker). A club's move or rebrand means editing :data:`CLUBS`; the source's start-up check
logs any code the league's own team list does not carry, and any it carries that is not here.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from ..logos import logo as cached_logo
from ..nfl.teams import RGB, hex_rgb, text_on
from ..nhl.teams import team as nhl_team

# code -> (city, nickname, division, NHL parent)
CLUBS: dict[str, tuple[str, str, str, str]] = {
    "ABB": ("Abbotsford", "Canucks", "Pacific", "VAN"), "BAK": ("Bakersfield", "Condors", "Pacific", "EDM"),
    "BEL": ("Belleville", "Senators", "North", "OTT"), "CGY": ("Calgary", "Wranglers", "Pacific", "CGY"),
    "CHI": ("Chicago", "Wolves", "Central", "CAR"), "CLE": ("Cleveland", "Monsters", "North", "CBJ"),
    "CLT": ("Charlotte", "Checkers", "Atlantic", "FLA"), "COL": ("Colorado", "Eagles", "Pacific", "COL"),
    "CV": ("Coachella Valley", "Firebirds", "Pacific", "SEA"), "GR": ("Grand Rapids", "Griffins", "Central", "DET"),
    "HAM": ("Hamilton", "Hammers", "North", "NYI"), "HER": ("Hershey", "Bears", "Atlantic", "WSH"),
    "HFD": ("Hartford", "Wolf Pack", "Atlantic", "NYR"), "HSK": ("Henderson", "Silver Knights", "Pacific", "VGK"),
    "IA": ("Iowa", "Wild", "Central", "MIN"), "LAV": ("Laval", "Rocket", "North", "MTL"),
    "LV": ("Lehigh Valley", "Phantoms", "Atlantic", "PHI"), "MB": ("Manitoba", "Moose", "Central", "WPG"),
    "MIL": ("Milwaukee", "Admirals", "Central", "NSH"), "ONT": ("Ontario", "Reign", "Pacific", "LAK"),
    "PRO": ("Providence", "Bruins", "Atlantic", "BOS"), "RFD": ("Rockford", "IceHogs", "Central", "CHI"),
    "ROC": ("Rochester", "Americans", "North", "BUF"), "SD": ("San Diego", "Gulls", "Pacific", "ANA"),
    "SJ": ("San Jose", "Barracuda", "Pacific", "SJS"), "SPR": ("Springfield", "Thunderbirds", "Atlantic", "STL"),
    "SYR": ("Syracuse", "Crunch", "North", "TBL"), "TEX": ("Texas", "Stars", "Central", "DAL"),
    "TOR": ("Toronto", "Marlies", "North", "TOR"), "TUC": ("Tucson", "Roadrunners", "Pacific", "UTA"),
    "UTC": ("Utica", "Comets", "North", "NJD"), "WBS": ("Wilkes-Barre/Scranton", "Penguins", "Atlantic", "PIT"),
}
# Affiliates whose sweater is not the parent's (primary, alternate).
OWN_COLORS: dict[str, tuple[str, str]] = {
    "COL": ("0b2240", "f5b335"), "CV": ("c8102e", "f26522"), "HER": ("4e2a1e", "ffffff"), "MIL": ("041e42", "8fd6ff"),
    "ROC": ("c8102e", "ffffff"), "TUC": ("9e1b32", "ffffff"), "SD": ("f47920", "041e42"),
}
DIVISIONS: dict[str, list[str]] = {}
for _code, (_, _, _div, _) in CLUBS.items():
    DIVISIONS.setdefault(_div, []).append(_code)
CONFERENCE_OF_DIVISION = {"Atlantic": "Eastern", "North": "Eastern", "Central": "Western", "Pacific": "Western"}
DIVISION_OF = {t: d for d, ts in DIVISIONS.items() for t in ts}
AHL_TEAMS = tuple(sorted(CLUBS))


def full_name(abbrev: str) -> str:
    city, nick, _, _ = CLUBS.get(abbrev.upper(), ("", abbrev, "", ""))
    return f"{city} {nick}".strip()


def colors(abbrev: str) -> tuple[RGB, RGB]:
    abbrev = abbrev.upper()
    if abbrev in OWN_COLORS:
        p, a = OWN_COLORS[abbrev]
        return hex_rgb(p), hex_rgb(a, (255, 255, 255))
    club = CLUBS.get(abbrev)
    if club is None:
        return (90, 90, 90), (255, 255, 255)
    parent = nhl_team(club[3])
    return parent.primary, parent.accent


def logo(abbrev: str, size: int) -> Image.Image:
    """Logo scaled to fit a ``size`` square (RGBA), or a neutral tile until the fetch lands."""
    img = cached_logo("ahl", abbrev, size)
    return img if img is not None else _placeholder(abbrev, size)


def _placeholder(abbrev: str, size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (*colors(abbrev)[0], 255))
    ImageDraw.Draw(img).rectangle((0, 0, size - 1, size - 1), outline=(255, 255, 255, 255))
    return img


__all__ = ["AHL_TEAMS", "CLUBS", "CONFERENCE_OF_DIVISION", "DIVISIONS", "DIVISION_OF", "colors", "full_name", "logo", "text_on"]
