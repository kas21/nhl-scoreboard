"""FBS team registry: conferences as of the 2026 season, logos from the runtime cache, colours
learned from the API.

Abbreviations are ESPN's (they key the scoreboard, standings, logos and the favourites
picker). Realignment moves a few schools most years: edit :data:`CONFERENCES` and the
source's start-up check (``NcaafSource._check_teams``) logs any entry ESPN no longer lists,
and any FBS school ESPN lists that is missing here.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from ..logos import logo as cached_logo
from ..nfl.teams import RGB, hex_rgb, text_on

CONFERENCES: dict[str, list[str]] = {
    "ACC": ["BC", "CAL", "CLEM", "DUKE", "FSU", "GT", "LOU", "MIA", "NCST", "PITT", "SMU", "STAN", "SYR", "UNC", "UVA", "VT", "WAKE"],
    "Big 12": ["ARIZ", "ASU", "BAY", "BYU", "CIN", "COLO", "HOU", "ISU", "KSU", "KU", "OKST", "TCU", "TTU", "UCF", "UTAH", "WVU"],
    "Big Ten": ["ILL", "IND", "IOWA", "MD", "MICH", "MINN", "MSU", "NEB", "NW", "ORE", "OSU", "PSU", "PUR", "RUTG", "UCLA", "USC", "WASH", "WIS"],
    "SEC": ["ALA", "ARK", "AUB", "FLA", "LSU", "MISS", "MIZ", "MSST", "OU", "SC", "TA&M", "TENN", "TEX", "UGA", "UK", "VAN"],
    "American": ["ARMY", "CLT", "ECU", "FAU", "MEM", "NAVY", "RICE", "TEM", "TLSA", "TULN", "UAB", "UNT", "USF", "UTSA"],
    "C-USA": ["DEL", "FIU", "JVST", "KENN", "LIB", "MOST", "MTSU", "NMSU", "SHSU", "WKU"],
    "MAC": ["AKR", "BALL", "BGSU", "BUFF", "CMU", "EMU", "KENT", "M-OH", "MASS", "OHIO", "TOL", "WMU"],
    "Mountain West": ["AFA", "HAW", "NEV", "NIU", "SJSU", "UNLV", "UNM", "UTEP", "WYO"],
    "Pac-12": ["BSU", "CSU", "FRES", "ORST", "SDSU", "TXST", "USU", "WSU"],
    "Sun Belt": ["APP", "ARST", "CCU", "GASO", "GAST", "JMU", "LT", "MRSH", "ODU", "TROY", "ULL", "ULM", "USA", "USM"],
    "Independents": ["CONN", "ND"],
}
CONFERENCE_OF = {t: c for c, ts in CONFERENCES.items() for t in ts}
NCAAF_TEAMS = tuple(sorted(CONFERENCE_OF))
_colors: dict[str, tuple[RGB, RGB]] = {}     # abbrev -> (primary, alternate), filled by the source


def learn_colors(abbrev: str, primary: str | None, alternate: str | None) -> None:
    _colors[abbrev.upper()] = (hex_rgb(primary), hex_rgb(alternate, (255, 255, 255)))


def colors(abbrev: str) -> tuple[RGB, RGB]:
    return _colors.get(abbrev.upper(), ((90, 90, 90), (255, 255, 255)))


def logo(abbrev: str, size: int) -> Image.Image:
    """Logo scaled to fit a ``size`` square (RGBA), or a neutral tile until the fetch lands."""
    img = cached_logo("ncaaf", abbrev, size)
    return img if img is not None else _placeholder(abbrev, size)


def _placeholder(abbrev: str, size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (*colors(abbrev)[0], 255))
    ImageDraw.Draw(img).rectangle((0, 0, size - 1, size - 1), outline=(255, 255, 255, 255))
    return img


__all__ = ["CONFERENCES", "CONFERENCE_OF", "NCAAF_TEAMS", "colors", "learn_colors", "logo", "text_on"]
