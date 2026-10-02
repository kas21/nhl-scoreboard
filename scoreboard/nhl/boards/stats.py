"""Game stats board: the two teams' numbers side by side, each on a tug-of-war bar.

One row per stat — shots, faceoffs, hits, blocked shots, PIM, power play, giveaways,
takeaways, then shots (and goals) by period — with the away value at the left, the home
value at the right and a bar between them split at the away side's share: the more of the
stat a team has, the more of the bar is in its colour. When a page comes up the split
starts at the middle and eases to its place, row after row, so the weight of each number
is seen moving. Pages hold as many rows as the panel has room for; the playlist's seconds
are seconds per page.
"""
from __future__ import annotations

from functools import lru_cache
from math import ceil
from typing import Any

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext, per_item
from ...render import Text, load_font, render_node
from ...render.anim import ease_out_cubic
from ...render.fx import chip
from ..teams import team
from .replay import side_colors

RGB = tuple[int, int, int]
WHITE = (255, 255, 255)
LABEL = (170, 170, 170)
TRACK = (36, 36, 36)
HEADER_H = 8
SLIDE_SECONDS = 0.6
STAGGER_SECONDS = 0.08


class GameStatsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="NHL game stats")
    seconds_per_page: float = Field(6.0, ge=2, le=30, description="Seconds each page of stats stays up (a playlist entry's seconds override it)")
    show_period_shots: bool = Field(True, description="Add a row of shots for each period")
    show_period_goals: bool = Field(False, description="Add a row of goals for each period")
    animate: bool = Field(True, description="Slide each bar's split from the middle to its place when a page comes up")


class Layout:
    """Row geometry for a panel size: text line, then the bar."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.tall = height >= 64
        self.row_h = 11 if self.tall else 8
        self.bar_h = 3 if self.tall else 2
        self.bar_y = 6 if self.tall else 5          # offset within the row
        self.rows_per_page = max((height - HEADER_H) // self.row_h, 1)
        self.bar_x0, self.bar_x1 = 1, width - 2
        self.short = width < 96

    def pitch(self, rows_on_fullest_page: int) -> int:
        """Row spacing: the rows spread down the panel rather than bunch under the header, but
        never further apart than a row and a short gap, and the same on every page."""
        avail = self.height - HEADER_H
        return max(min(avail // max(rows_on_fullest_page, 1), self.row_h + 3), self.row_h)


@lru_cache(maxsize=256)
def _text(text: str, color: RGB) -> Image.Image:
    return render_node(Text(text, load_font("pixel", 6), color))


@lru_cache(maxsize=16)
def header_image(width: int, away: str, home: str, score: str) -> Image.Image:
    img = Image.new("RGB", (width, HEADER_H), (0, 0, 0))
    a, h = team(away), team(home)
    font = load_font("pixel", 6)
    left = chip(away, font, a.text_on_primary, a.primary)
    right = chip(home, font, h.text_on_primary, h.primary)
    img.paste(left, (0, 0), left)
    img.paste(right, (width - right.width, 0), right)
    mid = _text(score, WHITE)
    img.paste(mid, ((width - mid.width) // 2, 1), mid)
    return img


def pages_of(rows: list[dict[str, Any]], per_page: int) -> list[list[dict[str, Any]]]:
    """Rows chunked into pages of even size: eleven rows on a five-row panel are 4/4/3, not 5/5/1."""
    if not rows:
        return []
    n_pages = ceil(len(rows) / per_page)
    size = ceil(len(rows) / n_pages)
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def split_at(share: float, page_t: float, index: int, animate: bool) -> float:
    """Where the bar's split sits ``page_t`` seconds into a page: from the middle to ``share``,
    eased, row ``index`` starting a beat after the one above."""
    if not animate:
        return share
    k = ease_out_cubic(min(max((page_t - index * STAGGER_SECONDS) / SLIDE_SECONDS, 0.0), 1.0))
    return 0.5 + (share - 0.5) * k


class GameStatsBoard(BaseBoard):
    key = "nhl.game_stats"
    title = "NHL game stats"
    config_model = GameStatsConfig
    requires = frozenset({"nhl.game_stats"})       # not main_event: last night's numbers are a recap the next morning
    pace_unit = "page"

    def rows(self, ctx: BoardContext, cfg: GameStatsConfig) -> list[dict[str, Any]]:
        value = ctx.snapshot.get("nhl.game_stats") or {}
        rows = list(value.get("stats") or [])
        if cfg.show_period_shots:
            rows.extend(value.get("shots_by_period") or [])
        if cfg.show_period_goals:
            rows.extend(value.get("goals_by_period") or [])
        return rows

    def pages(self, ctx: BoardContext, cfg: GameStatsConfig) -> list[list[dict[str, Any]]]:
        return pages_of(self.rows(ctx, cfg), Layout(ctx.width, ctx.height).rows_per_page)

    def auto_seconds(self, ctx: BoardContext, cfg: GameStatsConfig) -> float | None:
        n = len(self.pages(ctx, cfg))
        return n * per_item(ctx, cfg.seconds_per_page) if n else None

    def auto_items(self, ctx: BoardContext, cfg: GameStatsConfig) -> tuple[int, str] | None:
        n = len(self.pages(ctx, cfg))
        return (n, "page") if n else None

    def done(self, ctx: BoardContext, cfg: GameStatsConfig) -> bool:
        total = self.auto_seconds(ctx, cfg)
        return total is None or ctx.elapsed >= total

    def render(self, ctx: BoardContext, cfg: GameStatsConfig) -> Image.Image:
        value = ctx.snapshot.get("nhl.game_stats") or {}
        lay = Layout(ctx.width, ctx.height)
        img = Image.new("RGB", (ctx.width, ctx.height), (0, 0, 0))
        pages = self.pages(ctx, cfg)
        if not pages:
            return img
        secs = per_item(ctx, cfg.seconds_per_page)
        index = min(int(ctx.elapsed // secs), len(pages) - 1)
        page_t = ctx.elapsed - index * secs
        away, home = value.get("away", {}).get("abbrev", ""), value.get("home", {}).get("abbrev", "")
        main = ctx.snapshot.get("main_event") or {}
        live = main.get("id") == value.get("game_id") and main.get("phase") in ("live", "intermission")
        score = (f"{main['away'].get('score', 0)}-{main['home'].get('score', 0)}" if live     # mid-game: the score feed is fresher than the rail
                 else f"{value.get('away', {}).get('score', 0)}-{value.get('home', {}).get('score', 0)}")
        img.paste(header_image(ctx.width, away, home, score), (0, 0))
        away_c, home_c = side_colors(away, home)
        d = ImageDraw.Draw(img)
        pitch = lay.pitch(max(len(p) for p in pages))
        for i, row in enumerate(pages[index]):
            y = HEADER_H + i * pitch
            self._row(img, d, lay, row, y, away_c, home_c, split_at(float(row.get("share", 0.5)), page_t, i, cfg.animate))
        return img

    @staticmethod
    def _row(img: Image.Image, d: ImageDraw.ImageDraw, lay: Layout, row: dict[str, Any], y: int,
             away_c: RGB, home_c: RGB, split: float) -> None:
        left = _text(str(row.get("away", "")), WHITE)
        right = _text(str(row.get("home", "")), WHITE)
        label = _text(str(row.get("short" if lay.short else "label", "")), LABEL)
        img.paste(left, (lay.bar_x0, y), left)
        img.paste(right, (lay.bar_x1 - right.width + 1, y), right)
        img.paste(label, ((lay.width - label.width) // 2, y), label)
        y0, y1 = y + lay.bar_y, y + lay.bar_y + lay.bar_h - 1
        d.rectangle((lay.bar_x0, y0, lay.bar_x1, y1), fill=TRACK)
        span = lay.bar_x1 - lay.bar_x0
        marker = lay.bar_x0 + int(round(split * span))
        if marker > lay.bar_x0:
            d.rectangle((lay.bar_x0, y0, marker - 1, y1), fill=away_c)
        if marker < lay.bar_x1:
            d.rectangle((marker + 1, y0, lay.bar_x1, y1), fill=home_c)
        d.rectangle((marker, y0, marker, y1), fill=WHITE)
