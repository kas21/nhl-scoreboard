"""Pre-rendered animation helpers. Each returns a list of frames (PIL images).

Boards build transitions once, then index by elapsed time — <1ms/frame at draw.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import Literal

from PIL import Image, ImageEnhance

Easing = Callable[[float], float]
Direction = Literal["left", "right", "up", "down"]


def ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * t)


def linear(t: float) -> float:
    return t


def slide_in(frame: Image.Image, frames: int, direction: Direction = "left", easing: Easing = ease_out_cubic) -> list[Image.Image]:
    """Slide ``frame`` into view from off-canvas."""
    w, h = frame.size
    out: list[Image.Image] = []
    for i in range(frames):
        t = easing((i + 1) / frames)
        dx = dy = 0
        if direction == "left":
            dx = int(-w * (1 - t))
        elif direction == "right":
            dx = int(w * (1 - t))
        elif direction == "up":
            dy = int(-h * (1 - t))
        else:
            dy = int(h * (1 - t))
        canvas = Image.new("RGB", frame.size, (0, 0, 0))
        canvas.paste(frame, (dx, dy))
        out.append(canvas)
    return out


def fade(frame: Image.Image, frames: int, start: float = 0.0, end: float = 1.0, easing: Easing = linear) -> list[Image.Image]:
    enhancer = ImageEnhance.Brightness(frame)
    return [enhancer.enhance(start + (end - start) * easing((i + 1) / frames)) for i in range(frames)]


def flash(frame: Image.Image, color: tuple[int, int, int], count: int, frames_per_flash: int) -> list[Image.Image]:
    solid = Image.new("RGB", frame.size, color)
    out: list[Image.Image] = []
    for _ in range(count):
        out.extend([solid] * frames_per_flash)
        out.extend([frame] * frames_per_flash)
    return out


def hold(frame: Image.Image, frames: int) -> list[Image.Image]:
    return [frame] * frames


def frame_at(frames: list[Image.Image], elapsed: float, fps: int, loop: bool = False) -> Image.Image:
    """Pick the frame for ``elapsed`` seconds; clamps or loops."""
    idx = max(int(elapsed * fps), 0)
    if loop:
        return frames[idx % len(frames)]
    return frames[min(idx, len(frames) - 1)]
