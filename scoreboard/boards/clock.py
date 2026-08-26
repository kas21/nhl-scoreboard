"""Clock board — port of the old one: cyan time, magenta date above-left and year below-right,
looping diagonal sheen on the time."""
from __future__ import annotations

from typing import Literal

from PIL import Image
from pydantic import BaseModel, Field

from ..render import Absolute, Sheen, Text, load_font, render_tree
from .base import BaseBoard, BoardContext


class ClockConfig(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}
    format: Literal["12h", "24h"] = Field("12h", description="Hour format")
    show_date: bool = True
    color: tuple[int, int, int] = Field((0, 150, 150), description="Time colour (RGB)")
    date_color: tuple[int, int, int] = Field((255, 0, 255), description="Date/year colour (RGB)")


class ClockBoard(BaseBoard):
    key = "clock"
    title = "Clock"
    config_model = ClockConfig

    def render(self, ctx: BoardContext, cfg: ClockConfig) -> Image.Image:
        w, h = ctx.width, ctx.height
        p = ctx.profile
        fmt = "%-I:%M" if cfg.format == "12h" else "%H:%M"
        clock_size = max(int(h * 15 / 64), 8)
        time_node = Text(ctx.now.strftime(fmt), load_font("clock", clock_size), tuple(cfg.color))
        tw, th = time_node.measure()
        cx, cy = (w - tw) // 2, (h - th) // 2
        items = [(Sheen(time_node, period=3.0, band=14, strength=0.5, delay=1.0), cx, cy, tw, th)]
        if cfg.show_date:
            f6 = load_font("pl", p.font_small)
            date = Text(ctx.now.strftime("%b %d").upper(), f6, tuple(cfg.date_color))
            year = Text(ctx.now.strftime("%Y"), f6, tuple(cfg.date_color))
            dw, dh = date.measure()
            yw, yh = year.measure()
            items.append((date, cx, cy - dh - 1, dw, dh))
            items.append((year, cx + tw - yw, cy + th + 1, yw, yh))
        return render_tree(Absolute(items), w, h, t=ctx.elapsed)
