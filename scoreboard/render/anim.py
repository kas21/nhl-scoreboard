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


# -- Sequence: whole-frame transitions declared in seconds --------------------

Step = Callable[[Image.Image, int], list[Image.Image]]


class Sequence:
    """Declarative, finite, whole-frame animation timeline.

        seq = Sequence(fps).flash(color, times=2, secs=0.3).slide_in("right", 0.5).hold(6).fade_out(0.5).build(still)
        seq.at(elapsed)      # frame for a time
        seq.duration         # seconds

    Steps compile to a frame list once (in ``build``), so playback is a lookup.
    """

    def __init__(self, fps: int) -> None:
        self.fps = max(int(fps), 1)
        self._steps: list[Step] = []
        self._frames: list[Image.Image] = []

    # -- building --------------------------------------------------------

    def _n(self, secs: float) -> int:
        return max(int(round(secs * self.fps)), 1)

    def step(self, fn: Step) -> Sequence:
        self._steps.append(fn)
        return self

    def hold(self, secs: float) -> Sequence:
        return self.step(lambda still, fps: hold(still, self._n(secs)))

    def slide_in(self, direction: Direction = "left", secs: float = 0.5, easing: Easing = ease_out_cubic) -> Sequence:
        return self.step(lambda still, fps: slide_in(still, self._n(secs), direction, easing))

    def fade_in(self, secs: float = 0.5) -> Sequence:
        return self.step(lambda still, fps: fade(still, self._n(secs), 0.0, 1.0))

    def fade_out(self, secs: float = 0.5) -> Sequence:
        return self.step(lambda still, fps: fade(still, self._n(secs), 1.0, 0.0))

    def flash(self, color: tuple[int, int, int], times: int = 2, secs: float = 0.3) -> Sequence:
        """Alternate solid ``color`` and black ``times`` times over ``secs`` total."""
        per = max(self._n(secs) // (2 * max(times, 1)), 1)
        return self.step(lambda still, fps: flash(Image.new("RGB", still.size, (0, 0, 0)), color, times, per))

    def frames(self, frames: list[Image.Image], secs: float | None = None) -> Sequence:
        """Insert pre-made frames (e.g. a GIF); resampled to ``secs`` if given."""
        def _fn(still, fps):
            if secs is None or not frames:
                return list(frames)
            n = self._n(secs)
            return [frames[int(i * len(frames) / n)] for i in range(n)]
        return self.step(_fn)

    def build(self, still: Image.Image) -> Sequence:
        self._frames = [f for step in self._steps for f in step(still, self.fps)]
        return self

    # -- playback --------------------------------------------------------

    @property
    def duration(self) -> float:
        return len(self._frames) / self.fps

    def __len__(self) -> int:
        return len(self._frames)

    def at(self, elapsed: float) -> Image.Image:
        if not self._frames:
            raise ValueError("Sequence.build() has not been called")
        return frame_at(self._frames, elapsed, self.fps)

    def finished(self, elapsed: float) -> bool:
        return elapsed >= self.duration


def gif_frames(path: str, size: tuple[int, int] | None = None) -> list[Image.Image]:
    """Load every frame of a GIF as RGB (optionally resized to ``size``)."""
    from PIL import ImageSequence

    out = []
    with Image.open(path) as im:
        for frame in ImageSequence.Iterator(im):
            f = frame.convert("RGB")
            if size:
                f = f.resize(size, Image.NEAREST)
            out.append(f)
    return out
