"""Test pattern for wiring checks: RGB/white bars, a border, and a TOP-LEFT marker."""
from __future__ import annotations

from PIL import Image, ImageDraw

from .base import BaseBoard, BoardContext


class TestPatternBoard(BaseBoard):
    key = "test_pattern"
    title = "Test pattern"

    def render(self, ctx: BoardContext, cfg) -> Image.Image:
        w, h = ctx.width, ctx.height
        img = Image.new("RGB", (w, h), (0, 0, 0))
        d = ImageDraw.Draw(img)
        bars = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
        bw = w // len(bars)
        for i, c in enumerate(bars):
            d.rectangle((i * bw, h // 3, (i + 1) * bw - 1, h - 1), fill=c)
        d.rectangle((0, 0, w - 1, h - 1), outline=(255, 255, 0))
        f = ctx.profile.label_font()
        d.text((2, 2), "TOP LEFT", font=f, fill=(255, 255, 255))
        d.text((2, 9), "R  G  B  W", font=f, fill=(200, 200, 200))
        # blinking corner pixel bottom-right so a frozen panel is obvious
        if int(ctx.elapsed * 2) % 2 == 0:
            d.rectangle((w - 3, h - 3, w - 2, h - 2), fill=(255, 0, 255))
        return img
