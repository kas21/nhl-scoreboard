"""Tiny declarative layout engine on top of Pillow.

    tree = VBox([Text("TOR", font), HBox([Img(logo), Spacer(), Text("3", big)])])
    frame = render_tree(tree, 128, 64)

Nodes measure their natural size, then containers assign integer rects.
No event loop, no widgets, no hidden state — a pure function of inputs.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from .text import text_box

Align = Literal["start", "center", "end"]
RGB = tuple[int, int, int]
Placed = tuple[Image.Image, int, int]


def _align(offset_space: int, align: Align) -> int:
    if align == "center":
        return offset_space // 2
    if align == "end":
        return offset_space
    return 0


class Node:
    def measure(self) -> tuple[int, int]:
        raise NotImplementedError

    def place(self, x: int, y: int, w: int, h: int) -> Iterator[Placed]:
        raise NotImplementedError


@dataclass
class Spacer(Node):
    """Flexible gap; ``weight`` shares leftover space among spacers."""

    weight: int = 1
    min: int = 0

    def measure(self) -> tuple[int, int]:
        return (self.min, self.min)

    def place(self, x, y, w, h):
        return iter(())


@dataclass
class Img(Node):
    image: Image.Image

    def measure(self) -> tuple[int, int]:
        return self.image.size

    def place(self, x, y, w, h):
        yield (self.image, x + _align(w - self.image.width, "center"), y + _align(h - self.image.height, "center"))


@dataclass
class Text(Node):
    text: str
    font: ImageFont.ImageFont
    fill: RGB = (255, 255, 255)
    antialias: bool = False
    _size: tuple[int, int] = field(init=False, repr=False)
    _origin: tuple[int, int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        left, top, right, bottom = text_box(self.text, self.font, self.antialias)
        self._size = (int(right - left), int(bottom - top))
        self._origin = (-int(left), -int(top))

    def measure(self) -> tuple[int, int]:
        return self._size

    def place(self, x, y, w, h):
        img = Image.new("RGBA", (max(self._size[0], 1), max(self._size[1], 1)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        if not self.antialias:
            draw.fontmode = "1"
        draw.text(self._origin, self.text, font=self.font, fill=self.fill, anchor="la")
        yield (img, x + _align(w - self._size[0], "center"), y + _align(h - self._size[1], "center"))


@dataclass
class Box(Node):
    """Solid or empty rectangle with optional fixed size."""

    width: int = 0
    height: int = 0
    fill: tuple[int, int, int, int] | None = None

    def measure(self):
        return (self.width, self.height)

    def place(self, x, y, w, h):
        if self.fill:
            yield (Image.new("RGBA", (max(w, 1), max(h, 1)), self.fill), x, y)


@dataclass
class _Linear(Node):
    children: Sequence[Node]
    spacing: int = 0
    align: Align = "center"
    horizontal: bool = True

    def _sizes(self) -> list[tuple[int, int]]:
        return [c.measure() for c in self.children]

    def measure(self) -> tuple[int, int]:
        sizes = self._sizes()
        if not sizes:
            return (0, 0)
        main = sum(s[0] if self.horizontal else s[1] for s in sizes) + self.spacing * (len(sizes) - 1)
        cross = max(s[1] if self.horizontal else s[0] for s in sizes)
        return (main, cross) if self.horizontal else (cross, main)

    def place(self, x, y, w, h):
        sizes = self._sizes()
        total_main = w if self.horizontal else h
        used = sum(s[0] if self.horizontal else s[1] for s in sizes) + self.spacing * max(len(sizes) - 1, 0)
        spacers = [c for c in self.children if isinstance(c, Spacer)]
        extra = max(total_main - used, 0)
        weight_total = sum(s.weight for s in spacers) or 0
        cursor = 0
        for child, (cw, ch) in zip(self.children, sizes):
            main = cw if self.horizontal else ch
            if isinstance(child, Spacer) and weight_total:
                main += extra * child.weight // weight_total
            if self.horizontal:
                cy = y + _align(h - ch, self.align)
                yield from child.place(x + cursor, cy, main, ch)
            else:
                cx = x + _align(w - cw, self.align)
                yield from child.place(cx, y + cursor, cw, main)
            cursor += main + self.spacing


def HBox(children: Sequence[Node], spacing: int = 0, align: Align = "center") -> _Linear:
    return _Linear(list(children), spacing, align, horizontal=True)


def VBox(children: Sequence[Node], spacing: int = 0, align: Align = "center") -> _Linear:
    return _Linear(list(children), spacing, align, horizontal=False)


@dataclass
class Stack(Node):
    """Children drawn on top of each other, each centered in the box."""

    children: Sequence[Node]

    def measure(self):
        sizes = [c.measure() for c in self.children] or [(0, 0)]
        return (max(s[0] for s in sizes), max(s[1] for s in sizes))

    def place(self, x, y, w, h):
        for child in self.children:
            yield from child.place(x, y, w, h)


@dataclass
class Anchor(Node):
    """Pin a child to a position inside the box (e.g. top-left, bottom-right)."""

    child: Node
    h: Align = "center"
    v: Align = "center"
    dx: int = 0
    dy: int = 0

    def measure(self):
        return self.child.measure()

    def place(self, x, y, w, h):
        cw, ch = self.child.measure()
        yield from self.child.place(x + _align(w - cw, self.h) + self.dx, y + _align(h - ch, self.v) + self.dy, cw, ch)


def render_tree(root: Node, width: int, height: int, background: RGB = (0, 0, 0)) -> Image.Image:
    """Lay out ``root`` to fill the canvas and composite into an RGB image."""
    canvas = Image.new("RGBA", (width, height), (*background, 255))
    for image, x, y in root.place(0, 0, width, height):
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        canvas.alpha_composite(image, (int(x), int(y)))
    return canvas.convert("RGB")
