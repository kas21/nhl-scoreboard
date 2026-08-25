"""Shared building blocks for NHL boards."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from PIL import Image, ImageDraw

from ...render import Anchor, HBox, Img, Spacer, Stack, Text, VBox, load_font
from ...render.layout import Box, Node
from ...render.profiles import SizeProfile
from ..teams import logo, team

WHITE = (255, 255, 255)
GREY = (190, 190, 190)     # secondary text — LEDs need far more than screen-grey to read
DIM = (120, 120, 120)
YELLOW = (255, 200, 0)
RED = (230, 40, 40)


def local_time(iso_utc: str, tz) -> datetime | None:
    if not iso_utc:
        return None
    try:
        return datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(tz)
    except ValueError:
        return None


def fmt_time(dt: datetime | None, fmt24: bool = False) -> str:
    if dt is None:
        return ""
    return dt.strftime("%H:%M" if fmt24 else "%-I:%M%p").replace("AM", "am").replace("PM", "pm")


def fmt_date(iso_date: str) -> str:
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%b %-d").upper()
    except ValueError:
        return iso_date


def team_logo(abbrev: str, size: int) -> Img:
    return Img(logo(abbrev, size))


def team_block(abbrev: str, p: SizeProfile, width: int) -> Node:
    """Logo over abbrev, for compact layouts."""
    return VBox([team_logo(abbrev, p.logo), Text(abbrev, load_font("pixel", p.font_small), WHITE)], spacing=1)


def color_bar(abbrev: str, width: int, height: int) -> Img:
    t = team(abbrev)
    img = Image.new("RGBA", (max(width, 1), max(height, 1)), (*t.primary, 255))
    ImageDraw.Draw(img).line((0, 0, width, 0), fill=(*t.accent, 255))
    return Img(img)


def gradient_backdrop(width: int, height: int, away: str, home: str) -> Image.Image:
    """Team colours fading from each edge into black — cheap and looks good on LEDs."""
    img = Image.new("RGB", (width, height), (0, 0, 0))
    px = img.load()
    a, h = team(away).primary, team(home).primary
    span = max(width // 3, 1)
    for x in range(span):
        k = (1 - x / span) * 0.6
        col_a = tuple(int(c * k) for c in a)
        col_h = tuple(int(c * k) for c in h)
        for y in range(height):
            px[x, y] = col_a
            px[width - 1 - x, y] = col_h
    return img


def score_text(value: int, p: SizeProfile, color=WHITE) -> Text:
    return Text(str(value), load_font("score", p.font_score), color)


def indicator(text: str, p: SizeProfile, bg: tuple[int, int, int], fg=(0, 0, 0)) -> Node:
    """Small pill badge such as 'PP' or 'EN'."""
    font = load_font("pixel", p.font_small)
    t = Text(text, font, fg)
    w, h = t.measure()
    return Stack([Box(w + 4, h + 2, (*bg, 255)), t])


def matchup_row(game: dict[str, Any], p: SizeProfile, center: Node, width: int) -> Node:
    """[away logo] [center] [home logo] with the centre widget flexing."""
    return HBox([team_logo(game["away"]["abbrev"], p.logo), Spacer(), center, Spacer(), team_logo(game["home"]["abbrev"], p.logo)])


def footer(text: str, p: SizeProfile, color=GREY) -> Node:
    return Anchor(Text(text, load_font("pixel", p.font_small), color), v="end")


def utc_now() -> datetime:
    return datetime.now(UTC)
