"""Whole-frame transitions between boards. Pure: (style, prev, new, progress) -> frame."""
from __future__ import annotations

from typing import Literal

from PIL import Image

from ..render.anim import ease_in_out

Style = Literal["none", "fade", "slide_left", "slide_right", "slide_up", "slide_down", "wipe", "blinds"]
STYLES: tuple[Style, ...] = ("none", "fade", "slide_left", "slide_right", "slide_up", "slide_down", "wipe", "blinds")


def transition(style: Style, prev: Image.Image, new: Image.Image, progress: float) -> Image.Image:
    """Blend ``prev`` into ``new``; progress 0 = all prev, 1 = all new."""
    p = min(max(progress, 0.0), 1.0)
    if style == "none" or p >= 1.0:
        return new
    if p <= 0.0:
        return prev
    w, h = new.size
    k = ease_in_out(p)
    if style == "fade":
        return Image.blend(prev, new, k)
    if style.startswith("slide_"):
        out = Image.new("RGB", (w, h))
        dx = dy = 0
        if style == "slide_left":
            dx = -int(w * k)
        elif style == "slide_right":
            dx = int(w * k)
        elif style == "slide_up":
            dy = -int(h * k)
        else:
            dy = int(h * k)
        out.paste(prev, (dx, dy))
        out.paste(new, (dx - w if dx < 0 else dx + w if dx > 0 else 0, dy - h if dy < 0 else dy + h if dy > 0 else 0))
        return out
    if style == "wipe":
        out = prev.copy()
        edge = int(w * k)
        if edge > 0:
            out.paste(new.crop((0, 0, edge, h)), (0, 0))
        return out
    if style == "blinds":
        out = prev.copy()
        slat = max(h // 8, 2)
        reveal = int(slat * k)
        for top in range(0, h, slat):
            if reveal > 0:
                out.paste(new.crop((0, top, w, min(top + reveal, h))), (0, top))
        return out
    return new
