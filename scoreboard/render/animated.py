"""Animated layout nodes: element-level, continuous motion inside a tree.

Each node wraps a child, pre-renders its material once (cached by the child's
structure), and produces a cheap crop / composite per frame from ``t``:

    HBox([Marquee(Text(long_name, f), width=60, speed=20),
          Sheen(badge, period=2.0),
          Pulse(Text("EN", f), period=1.0)])

Static neighbours in the tree stay cached; only these nodes do per-frame work.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image, ImageEnhance

from .anim import Easing, ease_out_cubic
from .layout import Node, _cache_get, _cache_put, render_node

Direction = Literal["left", "right", "up", "down"]


class AnimatedNode(Node):
    child: Node
    h_align: str = "center"      # how the child's image sits inside the box: start | center | end
    v_align: str = field(default="center", kw_only=True)

    def _pos(self, x: int, y: int, w: int, h: int, img: Image.Image) -> tuple[int, int]:
        ox = 0 if self.h_align == "start" else (w - img.width) if self.h_align == "end" else (w - img.width) // 2
        oy = 0 if self.v_align == "start" else (h - img.height) if self.v_align == "end" else (h - img.height) // 2
        return x + ox, y + oy

    @property
    def is_static(self) -> bool:
        return False

    def cache_key(self):
        return None

    def measure(self) -> tuple[int, int]:
        return self.child.measure()

    def _material(self, tag: str, build: Callable[[], Image.Image], *extra: Hashable) -> Image.Image:
        """Per-child pre-rendered material, cached across frames when the child is static."""
        ck = self.child.cache_key()
        if ck is None or not self.child.is_static:
            return build()
        key = (tag, ck, *extra)
        img = _cache_get(key)
        if img is None:
            img = _cache_put(key, build())
        return img

    def _child_image(self) -> Image.Image:
        return self._material("node", lambda: render_node(self.child))


def _phase(t: float, period: float) -> float:
    return (t % period) / period if period > 0 else 0.0


@dataclass
class Marquee(AnimatedNode):
    """Scroll a too-wide child horizontally inside ``width``; passes through if it fits."""

    child: Node
    h_align: str = field(default="center", kw_only=True)
    v_align: str = field(default="center", kw_only=True)
    width: int
    speed: float = 20.0          # px/s
    gap: int = 12
    pause: float = 1.0           # seconds to hold before the first scroll

    def measure(self):
        cw, ch = self.child.measure()
        return (min(cw, self.width), ch)

    def place(self, x, y, w, h, t=0.0):
        img = self._child_image()
        if img.width <= self.width:
            yield (img, *self._pos(x, y, w, h, img))
            return
        cycle = img.width + self.gap
        strip = self._material("marquee", lambda: self._strip(img, cycle), self.gap)
        offset = 0 if t < self.pause else int(((t - self.pause) * self.speed) % cycle)
        view = strip.crop((offset, 0, offset + self.width, img.height))
        yield (view, x + (w - self.width) // 2, y + (h - img.height) // 2)

    @staticmethod
    def _strip(img: Image.Image, cycle: int) -> Image.Image:
        strip = Image.new("RGBA", (cycle * 2, img.height), (0, 0, 0, 0))
        strip.alpha_composite(img, (0, 0))
        strip.alpha_composite(img, (cycle, 0))
        return strip


@dataclass
class Sheen(AnimatedNode):
    """A soft highlight band sweeping across the child (only where the child is opaque).

    ``diagonal`` sweeps along x+y like the old client's ByteFX sheen; ``once`` plays a
    single sweep starting at ``delay`` seconds (e.g. right after an entrance) and then
    shows the plain child. ``reverse`` sweeps bottom-right -> top-left.
    """

    child: Node
    h_align: str = field(default="center", kw_only=True)
    v_align: str = field(default="center", kw_only=True)
    period: float = 2.0
    band: int = 8
    strength: float = 0.7
    steps: int = 24              # quantised phases -> effectively pre-rendered
    diagonal: bool = True
    once: bool = False
    delay: float = 0.0
    reverse: bool = False

    def place(self, x, y, w, h, t=0.0):
        img = self._child_image()
        local = t - self.delay
        if local < 0 or (self.once and local >= self.period):
            yield (img, *self._pos(x, y, w, h, img))
            return
        step = int(_phase(local, self.period) * self.steps) % self.steps
        if self.reverse:
            step = self.steps - 1 - step
        frame = self._material("sheen", lambda: self._frame(img, step), self.band, self.strength, self.steps, self.diagonal, step)
        yield (frame, *self._pos(x, y, w, h, img))

    def _frame(self, img: Image.Image, step: int) -> Image.Image:
        wdt, hgt = img.size
        span = (wdt + hgt) if self.diagonal else wdt
        travel = span + self.band
        pos = int(step / self.steps * travel) - self.band
        band = Image.new("L", img.size, 0)
        px = band.load()
        for yy in range(hgt):
            for xx in range(wdt):
                d = (xx + yy if self.diagonal else xx) - pos
                if 0 <= d < self.band:
                    k = 1 - abs((d + 0.5) / self.band * 2 - 1)      # triangle profile
                    px[xx, yy] = int(255 * self.strength * k)
        alpha = img.getchannel("A")
        mask = Image.eval(band, lambda v: v)                    # copy
        mask.paste(0, mask=Image.eval(alpha, lambda a: 255 - a))
        out = img.copy()
        out.paste(Image.new("RGBA", img.size, (255, 255, 255, 255)), (0, 0), mask)
        return out


@dataclass
class Pulse(AnimatedNode):
    """Brightness breathing between ``low`` and ``high``."""

    child: Node
    h_align: str = field(default="center", kw_only=True)
    v_align: str = field(default="center", kw_only=True)
    period: float = 1.0
    low: float = 0.35
    high: float = 1.0
    steps: int = 16

    def place(self, x, y, w, h, t=0.0):
        img = self._child_image()
        k = 0.5 - 0.5 * math.cos(2 * math.pi * _phase(t, self.period))
        level = int(k * (self.steps - 1))
        frame = self._material("pulse", lambda: self._frame(img, level), self.low, self.high, self.steps, level)
        yield (frame, *self._pos(x, y, w, h, img))

    def _frame(self, img: Image.Image, level: int) -> Image.Image:
        factor = self.low + (self.high - self.low) * level / max(self.steps - 1, 1)
        rgb = ImageEnhance.Brightness(img.convert("RGB")).enhance(factor)
        out = rgb.convert("RGBA")
        out.putalpha(img.getchannel("A"))
        return out


@dataclass
class Blink(AnimatedNode):
    child: Node
    h_align: str = field(default="center", kw_only=True)
    v_align: str = field(default="center", kw_only=True)
    period: float = 1.0
    duty: float = 0.5

    def place(self, x, y, w, h, t=0.0):
        if _phase(t, self.period) < self.duty:
            yield from self.child.place(x, y, w, h, t)


@dataclass
class Slide(AnimatedNode):
    """Finite box-local wipe: the child slides into (or, with ``out``, out of) its own box.

    Travel is the box size in ``direction`` and the movement is clipped to the box,
    matching the old client's ByteFX slide. ``out`` plays the reverse (exit) motion.
    """

    child: Node
    h_align: str = field(default="center", kw_only=True)
    v_align: str = field(default="center", kw_only=True)
    duration: float = 0.5
    direction: Direction = "left"
    delay: float = 0.0
    easing: Easing = field(default=ease_out_cubic)
    out: bool = False

    def place(self, x, y, w, h, t=0.0):
        img = self._child_image()
        p = 1.0 if self.duration <= 0 else min(max((t - self.delay) / self.duration, 0.0), 1.0)
        k = 1 - self.easing(p)
        if self.out:
            k = 1 - k
        dx = dy = 0
        if self.direction == "left":
            dx = -int(w * k)
        elif self.direction == "right":
            dx = int(w * k)
        elif self.direction == "up":
            dy = -int(h * k)
        else:
            dy = int(h * k)
        if dx == 0 and dy == 0:
            yield (img, *self._pos(x, y, w, h, img))
            return
        box = Image.new("RGBA", (max(w, 1), max(h, 1)), (0, 0, 0, 0))
        px, py = self._pos(0, 0, w, h, img)
        box.paste(img, (px + dx, py + dy), img)                # paste clips negatives
        yield (box, x, y)


@dataclass
class Fade(AnimatedNode):
    """Finite opacity ramp from ``start`` to ``end`` over ``duration``."""

    child: Node
    h_align: str = field(default="center", kw_only=True)
    v_align: str = field(default="center", kw_only=True)
    duration: float = 0.5
    start: float = 0.0
    end: float = 1.0
    delay: float = 0.0
    steps: int = 16

    def place(self, x, y, w, h, t=0.0):
        img = self._child_image()
        p = 1.0 if self.duration <= 0 else min(max((t - self.delay) / self.duration, 0.0), 1.0)
        level = int((self.start + (self.end - self.start) * p) * (self.steps - 1) + 0.5)
        frame = self._material("fade", lambda: self._frame(img, level), self.steps, level)
        yield (frame, *self._pos(x, y, w, h, img))

    def _frame(self, img: Image.Image, level: int) -> Image.Image:
        out = img.copy()
        out.putalpha(img.getchannel("A").point(lambda a: a * level // max(self.steps - 1, 1)))
        return out
