"""Boot splash: name slides in, then holds."""
from __future__ import annotations

from PIL import Image
from pydantic import BaseModel, Field

from ..render import Spacer, Text, VBox, render_tree
from ..render.text import fit_font
from ..render.anim import frame_at, hold, slide_in
from .base import BaseBoard, BoardContext


class SplashConfig(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}
    text: str = Field("SCOREBOARD", max_length=24)
    color: tuple[int, int, int] = (255, 255, 255)


class SplashBoard(BaseBoard):
    key = "splash"
    title = "Splash"
    config_model = SplashConfig

    def __init__(self) -> None:
        self._frames: list[Image.Image] = []
        self._size: tuple[int, int] | None = None

    def enter(self, ctx: BoardContext, cfg: SplashConfig) -> None:
        p = ctx.profile
        words = cfg.text.split()
        usable = ctx.width - 2 * p.pad
        rows = [Text(w, fit_font(w, "block", usable, p.font_large), tuple(cfg.color)) for w in words]
        tree = VBox([Spacer(), *rows, Spacer()], spacing=1)
        still = render_tree(tree, ctx.width, ctx.height)
        self._frames = slide_in(still, frames=ctx.fps // 2, direction="left") + hold(still, ctx.fps)
        self._size = (ctx.width, ctx.height)

    def render(self, ctx: BoardContext, cfg: SplashConfig) -> Image.Image:
        if not self._frames or self._size != (ctx.width, ctx.height):
            self.enter(ctx, cfg)
        return frame_at(self._frames, ctx.elapsed, ctx.fps)
