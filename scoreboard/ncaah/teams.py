"""Division I men's hockey registry: conferences as of the 2026-27 season, logos from the
runtime cache, colours from a curated table (ESPN's hockey team API carries none).

Abbreviations are ESPN's *scoreboard* codes (they key the games and the favourites picker).
ESPN's team API, which supplies ids and logo URLs, spells two schools differently;
``logos.API_ABBREVS['ncaah']`` maps those. Realignment moves a school or two most years:
edit :data:`CONFERENCES`, and the source's start-up check (``NcaahSource._check_teams``)
logs any favourite ESPN's team list does not know.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from ..logos import API_ABBREVS as _API_ABBREVS
from ..logos import logo as cached_logo
from ..nfl.teams import RGB, hex_rgb, text_on

CONFERENCES: dict[str, list[str]] = {
    "Atlantic Hockey": ["AFA", "AIC", "ARMY", "BENT", "CAN", "HC", "MERC", "NIA", "RIT", "RMU", "SHU"],
    "Big Ten": ["MICH", "MINN", "MSU", "ND", "OSU", "PSU", "WISC"],
    "CCHA": ["AUSD", "BGSU", "BST", "FRST", "LSS", "MNST", "MTU", "NMI"],
    "ECAC": ["BRWN", "CLAR", "COLG", "COR", "DART", "HARV", "PRIN", "QUIN", "RPI", "UNNY", "USL", "YALE"],
    "Hockey East": ["BC", "BU", "CONN", "MASS", "ME", "MRMK", "NE", "PROV", "UML", "UNH", "UVM"],
    "NCHC": ["ASU", "COLC", "DEN", "M-OH", "OMA", "SCSU", "STMN", "UMD", "UND", "WMU"],
    "Independents": ["AKFB", "LIN", "LIU", "STO", "UAA"],
}
CONFERENCE_OF = {t: c for c, ts in CONFERENCES.items() for t in ts}
NCAAH_TEAMS = tuple(sorted(CONFERENCE_OF))
# Our code -> the one ESPN's team API uses, where they differ (the scoreboard uses ours).
API_ABBREVS: dict[str, str] = _API_ABBREVS["ncaah"]
REGISTRY_ABBREVS: dict[str, str] = {api: ours for ours, api in API_ABBREVS.items()}

# School colours (primary, alternate). ESPN's hockey feeds carry no colours, so these are curated.
COLORS: dict[str, tuple[str, str]] = {
    "AFA": ("003087", "b2b4b2"), "AIC": ("000000", "ffcc00"), "ARMY": ("000000", "d4bf91"), "BENT": ("003478", "ffffff"),
    "CAN": ("003da5", "ffc72c"), "HC": ("602d89", "ffffff"), "MERC": ("00754a", "ffffff"), "NIA": ("582c83", "ffffff"),
    "RIT": ("f76902", "000000"), "RMU": ("14234b", "a6192e"), "SHU": ("ce1141", "ffffff"),
    "MICH": ("00274c", "ffcb05"), "MINN": ("7a0019", "ffcc33"), "MSU": ("18453b", "ffffff"), "ND": ("0c2340", "c99700"),
    "OSU": ("bb0000", "666666"), "PSU": ("041e42", "ffffff"), "WISC": ("c5050c", "ffffff"),
    "AUSD": ("003a70", "ffd200"), "BGSU": ("4f2c1d", "fe5000"), "BST": ("004a35", "ffffff"), "FRST": ("ba0c2f", "ffc72c"),
    "LSS": ("00305d", "ffd300"), "MNST": ("4c1b7e", "ffc72c"), "MTU": ("000000", "ffcd00"), "NMI": ("006747", "ffcd00"),
    "BRWN": ("4e3629", "ffffff"), "CLAR": ("006633", "ffcc00"), "COLG": ("821019", "ffffff"), "COR": ("b31b1b", "ffffff"),
    "DART": ("00693e", "ffffff"), "HARV": ("a51c30", "ffffff"), "PRIN": ("e77500", "000000"), "QUIN": ("003b71", "ffc72c"),
    "RPI": ("d6001c", "ffffff"), "UNNY": ("7d1e2a", "ffffff"), "USL": ("b31b1b", "6a3b2a"), "YALE": ("00356b", "ffffff"),
    "BC": ("98002e", "bc9b6a"), "BU": ("cc0000", "ffffff"), "CONN": ("000e2f", "e4002b"), "MASS": ("881c1c", "ffffff"),
    "ME": ("003263", "b0d7ff"), "MRMK": ("002d62", "ffcc00"), "NE": ("cc0000", "000000"), "PROV": ("000000", "8a8d8f"),
    "UML": ("0067b1", "c8102e"), "UNH": ("003591", "ffffff"), "UVM": ("154734", "ffd200"),
    "ASU": ("8c1d40", "ffc627"), "COLC": ("000000", "ffcc00"), "DEN": ("8b2332", "8b6f4e"), "M-OH": ("c41230", "000000"),
    "OMA": ("d71920", "000000"), "SCSU": ("c8102e", "000000"), "STMN": ("510c76", "9d9d9d"), "UMD": ("7a0019", "ffcc33"),
    "UND": ("009a44", "000000"), "WMU": ("6c4023", "b5a167"),
    "AKFB": ("236192", "ffc72c"), "LIN": ("000000", "ffb81c"), "LIU": ("0a2240", "0093d0"), "STO": ("512d6d", "ffffff"),
    "UAA": ("00583d", "ffcc00"),
}
_learned: dict[str, tuple[RGB, RGB]] = {}     # should ESPN ever start sending colours, they win


def learn_colors(abbrev: str, primary: str | None, alternate: str | None) -> None:
    """Same hook the football registries expose; ESPN sends nothing for hockey today, so it is a no-op then."""
    if primary:
        _learned[abbrev.upper()] = (hex_rgb(primary), hex_rgb(alternate, (255, 255, 255)))


def colors(abbrev: str) -> tuple[RGB, RGB]:
    abbrev = abbrev.upper()
    if abbrev in _learned:
        return _learned[abbrev]
    p, a = COLORS.get(abbrev, ("5a5a5a", "ffffff"))
    return hex_rgb(p), hex_rgb(a, (255, 255, 255))


def logo(abbrev: str, size: int) -> Image.Image:
    """Logo scaled to fit a ``size`` square (RGBA), or a neutral tile until the fetch lands."""
    img = cached_logo("ncaah", abbrev, size)
    return img if img is not None else _placeholder(abbrev, size)


def _placeholder(abbrev: str, size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (*colors(abbrev)[0], 255))
    ImageDraw.Draw(img).rectangle((0, 0, size - 1, size - 1), outline=(255, 255, 255, 255))
    return img


__all__ = ["API_ABBREVS", "CONFERENCES", "CONFERENCE_OF", "NCAAH_TEAMS", "REGISTRY_ABBREVS", "colors", "learn_colors", "logo", "text_on"]
