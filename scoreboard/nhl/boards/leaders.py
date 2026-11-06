"""Leaders boards: who has the most goals, assists and points, and the top goalies.

Two boards on one renderer. ``nhl.leaders`` is the league's lists, each row led by the
player's team chip; ``nhl.team_leaders`` is the same four lists for each favourite club, under
a header in the club's colours. A category is a page, each has its own switch in the board's
settings, and the playlist's seconds are seconds per page.

A row is the name, then the number at the right, on a bar the length of that number against
the leader's (so McDavid's 44 points to the next man's 31 is seen, not just read), in the
player's team colour; the bars grow from the left as a page comes up. Goalies show wins,
goals-against and save percentage side by side where the panel is wide enough, ranked by
whichever the settings say, and carry a bar only when that is a count (wins, shutouts).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, ClassVar, Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext, per_item
from ...render.anim import ease_out_cubic
from ...render.fx import chip
from ...render.text import bitmap_safe, text_box
from ..teams import team
from .replay import NEUTRAL, lift

RGB = tuple[int, int, int]
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
NAME = (200, 200, 200)          # a player who is not on one of your teams (the standings' convention)
OTHER_STAT = (150, 150, 150)    # the goalie numbers the page is not ranked by
TRACK = (36, 36, 36)
HEADER_H = 7
TEXT_H = 5
ROW_MIN, ROW_MAX = 7, 11        # row pitch: packed, and as far apart as rows are let to spread
BAR_MIN_PITCH = 8               # a row needs this much for a bar under its text
GROW_SECONDS = 0.6
STAGGER_SECONDS = 0.08
RANK_W = 11                     # room for "10" and a gap, on a wide panel
COLUMN_GAP = 6

CATEGORIES: tuple[str, ...] = ("goals", "assists", "points", "goalies")
TITLES = {"goals": "GOALS", "assists": "ASSISTS", "points": "POINTS", "goalies": "GOALIES"}
GOALIE_LABELS = {"wins": "W", "gaa": "GAA", "save_pct": "SV%", "shutouts": "SO"}
GOALIE_COUNTS = ("wins", "shutouts")
PLAYOFFS = 3                    # the feed's gameType


class LeadersConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="NHL league leaders")
    goals: bool = Field(True, description="Show the goals leaders")
    assists: bool = Field(True, description="Show the assists leaders")
    points: bool = Field(True, description="Show the points leaders")
    goalies: bool = Field(True, description="Show the top goalies")
    count: int = Field(5, ge=1, le=10, description="Players on each page (fewer on a panel too short for that many)")
    goalie_rank: Literal["wins", "save_pct", "gaa", "shutouts"] = Field(
        "wins", description="What the goalies are ranked by: wins, save percentage, goals-against average or shutouts")
    seconds_per_page: float = Field(8.0, ge=2, le=30, description="Seconds each page stays up (a playlist entry's seconds override it)")
    animate: bool = Field(True, description="Grow each bar from the left when a page comes up")


class TeamLeadersConfig(LeadersConfig):
    model_config = ConfigDict(frozen=True, extra="forbid", title="NHL team leaders")


@dataclass(frozen=True)
class Page:
    team: str | None                    # None is the league's page
    category: str
    rows: tuple[dict[str, Any], ...]
    playoffs: bool = False


class Layout:
    """Where things sit for a panel size."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.wide = width >= 96
        self.top = HEADER_H + 1                                 # a dark line between the header bar and the first row
        self.fit = max((height - self.top) // ROW_MIN, 1)       # rows a page has room for
        self.right = width - 2                                  # where the numbers end

    def pitch(self, rows_on_fullest_page: int) -> int:
        """Row spacing: spread down the panel rather than bunched under the header, the same
        on every page of a run."""
        return max(min((self.height - self.top) // max(rows_on_fullest_page, 1), ROW_MAX), ROW_MIN)

    @property
    def chip_pad(self) -> tuple[int, int, int, int]:
        """The team chip's padding: the standings' on a wide panel, a pixel tighter each side on a narrow one."""
        return (2, 1, 3, 1) if self.wide else (1, 1, 2, 1)

    def name_x(self, league: bool, font) -> int:
        """Where the name (and the bar under it) starts: after the rank and, on the league's
        pages, the team chip. A narrow panel has no room for the rank beside a chip, and
        only single-digit ranks."""
        if league:
            return (RANK_W if self.wide else 0) + chip("WWW", font, WHITE, BLACK, pad=self.chip_pad).width + 2
        return RANK_W if self.wide else 8


def _put(img: Image.Image, text: str, font, color: RGB, x: int, y: int, right: bool = False) -> int:
    """Draw ``text`` with its ink's top-left at (x, y), or its top-right with ``right``. Returns the ink width."""
    if not text:
        return 0
    left, top, r, _ = text_box(text, font)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    d.text(((x - (r - left) if right else x) - left, y - top), text, font=font, fill=color)
    return r - left


def _width(text: str, font) -> int:
    left, _, right, _ = text_box(text, font)
    return right - left


@lru_cache(maxsize=64)
def bar_color(abbrev: str) -> RGB:
    """The team's colour for a bar on black: its primary, lifted if it is one of the navies."""
    t = team(abbrev)
    return lift(t.primary) or lift(t.accent) or NEUTRAL


def stat_text(stat: str, value: Any) -> str:
    """A goalie number as the league prints it: 10, 2.31, .921 — and a dash for one it does not rank."""
    if value is None:
        return "-"
    if stat == "save_pct":
        text = f"{float(value):.3f}"
        return text[1:] if text.startswith("0") else text
    if stat == "gaa":
        return f"{float(value):.2f}"
    return str(int(value))


def goalie_columns(rank: str, wide: bool) -> tuple[str, ...]:
    """The numbers a goalie row shows, left to right: all three on a wide panel (shutouts in
    place of wins when that is the ranking), only the one ranked by on a narrow one."""
    if not wide:
        return (rank,)
    return ("shutouts" if rank == "shutouts" else "wins", "gaa", "save_pct")


def column_rights(columns: tuple[str, ...], right: int, font) -> list[int]:
    """The right edge of each column, the last one at ``right``; each is as wide as its widest usual value."""
    widths = [_width({"save_pct": ".999", "gaa": "9.99"}.get(c, "99"), font) for c in columns]
    rights, x = [], right
    for w in reversed(widths):
        rights.append(x)
        x -= w + COLUMN_GAP
    return rights[::-1]


def names(rows: tuple[dict[str, Any], ...], room: int, font) -> list[str]:
    """What to call each row in ``room`` pixels: ``C. MCDAVID`` when every row of the page fits
    that way, otherwise last names, cut short where even those do not fit."""
    last = [bitmap_safe(str(r.get("last") or "")).upper() for r in rows]
    full = [f"{bitmap_safe(str(r.get('first') or ''))[:1].upper()}. {name}" if r.get("first") else name
            for r, name in zip(rows, last)]
    if all(_width(n, font) <= room for n in full):
        return full
    out = []
    for name in last:
        while name and _width(name, font) > room:
            name = name[:-1].rstrip(" -.'")
        out.append(name)
    return out


def grown(share: float, page_t: float, index: int, animate: bool) -> float:
    """How much of its length a bar has ``page_t`` seconds into a page: eased from nothing,
    row ``index`` starting a beat after the one above."""
    if not animate:
        return share
    return share * ease_out_cubic(min(max((page_t - index * STAGGER_SECONDS) / GROW_SECONDS, 0.0), 1.0))


class _LeadersBoard(BaseBoard):
    config_model = LeadersConfig
    pace_unit = "page"
    seasonal = True
    highlight_key: ClassVar[str] = "nhl.team_summary"      # its keys are the favourites

    # -- what to show ---------------------------------------------------------------

    def sources(self, ctx: BoardContext) -> list[tuple[str | None, dict[str, Any]]]:
        """(team or None for the league, its leaders value) for each set of pages, in order."""
        raise NotImplementedError

    def pages(self, ctx: BoardContext, cfg: LeadersConfig) -> list[Page]:
        keep = min(cfg.count, Layout(ctx.width, ctx.height).fit)
        pages: list[Page] = []
        for who, value in self.sources(ctx):
            playoffs = value.get("game_type") == PLAYOFFS
            for category in CATEGORIES:
                if not getattr(cfg, category):
                    continue
                rows = (value.get("goalies") or {}).get(cfg.goalie_rank) if category == "goalies" else value.get(category)
                if rows:
                    pages.append(Page(who, category, tuple(rows[:keep]), playoffs))
        return pages

    def auto_seconds(self, ctx: BoardContext, cfg: LeadersConfig) -> float | None:
        n = len(self.pages(ctx, cfg))
        return n * per_item(ctx, cfg.seconds_per_page) if n else None

    def auto_items(self, ctx: BoardContext, cfg: LeadersConfig) -> tuple[int, str] | None:
        n = len(self.pages(ctx, cfg))
        return (n, "page") if n else None

    def done(self, ctx: BoardContext, cfg: LeadersConfig) -> bool:
        total = self.auto_seconds(ctx, cfg)
        return total is None or ctx.elapsed >= total

    # -- drawing --------------------------------------------------------------------

    def render(self, ctx: BoardContext, cfg: LeadersConfig) -> Image.Image:
        img = Image.new("RGB", (ctx.width, ctx.height), BLACK)
        pages = self.pages(ctx, cfg)
        if not pages:
            return img
        secs = per_item(ctx, cfg.seconds_per_page)
        index = min(int(ctx.elapsed // secs), len(pages) - 1)
        page, page_t = pages[index], ctx.elapsed - index * secs
        lay = Layout(ctx.width, ctx.height)
        font = ctx.profile.label_font()
        league = page.team is None
        goalies = page.category == "goalies"
        columns = goalie_columns(cfg.goalie_rank, lay.wide) if goalies else ("value",)
        rights = column_rights(columns, lay.right, font) if goalies else [lay.right]
        self._header(img, lay, font, page, columns if goalies else (), rights)
        pitch = lay.pitch(max(len(p.rows) for p in pages))
        bar_h = 0 if pitch < BAR_MIN_PITCH else 1 if pitch < 10 else 2
        content_h = TEXT_H + (1 + bar_h if bar_h else 0)       # the same on every page, so the text does not shift between them
        has_bar = bool(bar_h) and (not goalies or cfg.goalie_rank in GOALIE_COUNTS)
        ranked = cfg.goalie_rank if goalies else "value"
        top_value = max((float(r.get(ranked) or 0) for r in page.rows), default=0.0)
        name_x = lay.name_x(league, font)
        first_left = rights[0] - max(_width(stat_text(columns[0], r.get(columns[0])) if goalies else str(r.get("value", "")), font)
                                     for r in page.rows)
        labels = names(page.rows, first_left - 4 - name_x, font)
        favourites = set(ctx.snapshot.get(self.highlight_key) or {})
        d = ImageDraw.Draw(img)
        for i, (row, label) in enumerate(zip(page.rows, labels)):
            y = lay.top + i * pitch + max((pitch - content_h) // 2, 1)     # a chip stands a pixel above its text: keep it off the header's line
            abbrev = str(row.get("team") or "")
            color = WHITE if not league or abbrev in favourites else NAME
            if lay.wide or not league:
                _put(img, str(i + 1), font, color, 1, y)
            if league:
                t = team(abbrev)
                tag = chip(abbrev, font, t.text_on_primary, t.primary, pad=lay.chip_pad)
                img.paste(tag, (name_x - 2 - tag.width, y - 1))
            _put(img, label, font, color, name_x, y)
            for column, right in zip(columns, rights):
                text = stat_text(column, row.get(column)) if goalies else str(row.get("value", ""))
                _put(img, text, font, WHITE if column == ranked else OTHER_STAT, right, y, right=True)
            if has_bar:
                y0 = y + TEXT_H + 1
                d.rectangle((name_x, y0, lay.right, y0 + bar_h - 1), fill=TRACK)
                share = float(row.get(ranked) or 0) / top_value if top_value > 0 else 0.0
                length = round(grown(share, page_t, i, cfg.animate) * (lay.right - name_x + 1))
                if length > 0:
                    d.rectangle((name_x, y0, name_x + length - 1, y0 + bar_h - 1), fill=bar_color(abbrev))
        return img

    def _header(self, img: Image.Image, lay: Layout, font, page: Page, columns: tuple[str, ...], rights: list[int]) -> None:
        """The bar across the top: white for the league, the club's colours for a club; the
        category at the left and, for goalies, a label over each column of numbers."""
        if page.team is None:
            who, fg, bg = "NHL", BLACK, WHITE
        else:
            t = team(page.team)
            who, fg, bg = page.team, t.text_on_primary, t.primary
        labels = [GOALIE_LABELS[c] for c in columns]
        room = (rights[0] - _width(labels[0], font) if labels else lay.width) - 4
        what = TITLES[page.category]
        titles = [f"{who} PLAYOFF {what}", f"{who} PO {what}"] if page.playoffs else [f"{who} {what}"]
        title = next((t for t in titles if _width(t, font) <= room), titles[-1])
        ImageDraw.Draw(img).rectangle((0, 0, lay.width - 1, HEADER_H - 1), fill=bg)
        _put(img, title, font, fg, 1, 1)
        for label, right in zip(labels, rights):
            _put(img, label, font, fg, right, 1, right=True)


class LeadersBoard(_LeadersBoard):
    key = "nhl.leaders"
    title = "NHL league leaders"
    requires = frozenset({"nhl.leaders"})

    def sources(self, ctx: BoardContext) -> list[tuple[str | None, dict[str, Any]]]:
        value = ctx.snapshot.get("nhl.leaders")
        return [(None, value)] if value else []


class TeamLeadersBoard(_LeadersBoard):
    key = "nhl.team_leaders"
    title = "NHL team leaders"
    config_model = TeamLeadersConfig
    requires = frozenset({"nhl.team_leaders"})

    def sources(self, ctx: BoardContext) -> list[tuple[str | None, dict[str, Any]]]:
        return [(abbrev, value) for abbrev, value in (ctx.snapshot.get("nhl.team_leaders") or {}).items() if value]
