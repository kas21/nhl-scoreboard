"""Boot splash: name slides in, then holds."""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..render import Sequence, Spacer, Text, VBox, fit_font, render_tree
from .base import BaseBoard, BoardContext, SequenceMixin


class SplashConfig(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}
    text: str = Field("SCOREBOARD", max_length=24)
    color: tuple[int, int, int] = (255, 255, 255)


class SplashBoard(SequenceMixin, BaseBoard):
    key = "splash"
    title = "Splash"
    config_model = SplashConfig

    def build(self, ctx: BoardContext, cfg: SplashConfig) -> Sequence:
        p = ctx.profile
        usable = ctx.width - 2 * p.pad
        rows = [Text(w, fit_font(w, "block", usable, p.font_large), tuple(cfg.color)) for w in cfg.text.split()]
        still = render_tree(VBox([Spacer(), *rows, Spacer()], spacing=1), ctx.width, ctx.height)
        return Sequence(ctx.fps).slide_in("left", 0.5).hold(60).build(still)
