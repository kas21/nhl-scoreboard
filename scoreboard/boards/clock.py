"""Clock board — port of the old one: cyan time, magenta date above-left and year below-right,
looping diagonal sheen on the time."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from PIL import Image, ImageFont
from pydantic import BaseModel, Field

from ..render import Absolute, Sheen, Text, load_font, render_tree
from ..render.text import text_size
from .base import BaseBoard, BoardContext

MIN_CLOCK = 8
DATE_RATIO = 0.4          # date/year height relative to the time, as the old client had it
WIDEST_TIME = "88:88"     # size for the widest time so the digits don't resize every minute
WIDEST_DATE, WIDEST_YEAR = "AUG 88", "8888"


class ClockConfig(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}
    format: Literal["12h", "24h"] = Field("12h", description="Hour format")
    show_date: bool = True
    color: tuple[int, int, int] = Field((0, 150, 150), description="Time colour (RGB)")
    date_color: tuple[int, int, int] = Field((255, 0, 255), description="Date/year colour (RGB)")


def _date_font(clock_size: int) -> ImageFont.ImageFont:
    """The old client's ``pl`` face, which ships only 6 px and 12 px — pick the nearer one."""
    size = max(6, round(clock_size * DATE_RATIO))
    if size >= 15:
        return load_font("pixelbold", size)
    return load_font("pl", 12 if size >= 9 else 6)


@lru_cache(maxsize=32)
def _fonts(width: int, height: int, pad: int, show_date: bool) -> tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
    """Largest clock face whose whole block (date / time / year) still fits the panel."""
    for size in range(height, MIN_CLOCK - 1, -1):
        clock = load_font("clock", size)
        date = _date_font(size)
        tw, th = text_size(WIDEST_TIME, clock)
        block_w, block_h = tw, th
        if show_date:
            dw, dh = text_size(WIDEST_DATE, date)
            yw, yh = text_size(WIDEST_YEAR, date)
            block_w, block_h = max(tw, dw, yw), th + dh + yh + 2
        if block_w <= width - 2 * pad and block_h <= height - 2 * pad:
            return clock, date
    return load_font("clock", MIN_CLOCK), _date_font(MIN_CLOCK)


class ClockBoard(BaseBoard):
    key = "clock"
    title = "Clock"
    config_model = ClockConfig

    def render(self, ctx: BoardContext, cfg: ClockConfig) -> Image.Image:
        w, h = ctx.width, ctx.height
        pad = ctx.profile.pad
        fmt = "%-I:%M" if cfg.format == "12h" else "%H:%M"
        clock_font, date_font = _fonts(w, h, pad, cfg.show_date)
        time_node = Text(ctx.now.strftime(fmt), clock_font, tuple(cfg.color))
        tw, th = time_node.measure()

        date = year = None
        dh = yh = 0
        if cfg.show_date:
            date = Text(ctx.now.strftime("%b %d").upper(), date_font, tuple(cfg.date_color))
            year = Text(ctx.now.strftime("%Y"), date_font, tuple(cfg.date_color))
            dh, yh = date.measure()[1], year.measure()[1]

        cx = (w - tw) // 2
        top = max(0, (h - (th + dh + yh + (2 if cfg.show_date else 0))) // 2)
        cy = top + (dh + 1 if cfg.show_date else 0)
        items = [(Sheen(time_node, period=3.0, band=max(14, th), strength=0.5, delay=1.0), cx, cy, tw, th)]
        if date is not None and year is not None:
            dw, yw = date.measure()[0], year.measure()[0]
            items.append((date, cx, top, dw, dh))
            items.append((year, cx + tw - yw, cy + th + 1, yw, yh))
        return render_tree(Absolute(items), w, h, t=ctx.elapsed)
