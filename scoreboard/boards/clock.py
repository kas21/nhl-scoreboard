"""Clock board: time, date, optional seconds. Also the ERROR fallback."""
from __future__ import annotations

from typing import Literal

from PIL import Image
from pydantic import BaseModel, Field

from ..render import Spacer, Text, VBox, load_font, render_tree
from ..render.text import fit_font
from .base import BaseBoard, BoardContext


class ClockConfig(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}
    format: Literal["12h", "24h"] = Field("12h", description="Hour format")
    show_seconds: bool = False
    show_date: bool = True
    color: tuple[int, int, int] = Field((255, 255, 255), description="Time colour (RGB)")
    date_color: tuple[int, int, int] = Field((190, 190, 190), description="Date colour (RGB)")


class ClockBoard(BaseBoard):
    key = "clock"
    title = "Clock"
    config_model = ClockConfig

    def render(self, ctx: BoardContext, cfg: ClockConfig) -> Image.Image:
        p = ctx.profile
        if cfg.format == "12h":
            fmt = "%-I:%M:%S" if cfg.show_seconds else "%-I:%M"
            time_text = ctx.now.strftime(fmt)
            suffix = ctx.now.strftime("%p")
        else:
            time_text = ctx.now.strftime("%H:%M:%S" if cfg.show_seconds else "%H:%M")
            suffix = ""
        clock_font = fit_font(time_text, "clock", ctx.width - 2 * p.pad, p.font_score)
        rows = [Text(time_text, clock_font, tuple(cfg.color))]
        if cfg.show_date:
            rows.append(Text(ctx.now.strftime("%a %b %-d"), load_font("pixel", p.font_medium), tuple(cfg.date_color)))
        if suffix:
            rows.insert(1, Text(suffix, load_font("pixel", p.font_small), tuple(cfg.date_color)))
        # Small panels: drop optional rows (AM/PM first, then date) until it fits.
        while len(rows) > 1 and VBox(rows, spacing=1).measure()[1] > ctx.height:
            rows.pop(1 if len(rows) == 3 else -1)
        return render_tree(VBox([Spacer(), *rows, Spacer()], spacing=1), ctx.width, ctx.height)
