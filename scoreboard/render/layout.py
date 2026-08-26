"""Tiny declarative layout engine on top of Pillow.

    tree = VBox([Text("TOR", font), HBox([Img(logo), Spacer(), Text("3", big)])])
    frame = render_tree(tree, 128, 64, t=elapsed)

Nodes measure their natural size, then containers assign integer rects.
Everything is a pure function of (tree, size, t): no event loop, no hidden
state. Motion comes from *animated nodes* (see ``animated.py``) whose image
depends on ``t``; static subtrees are rendered once and served from a cache,
so per-frame cost is proportional to what actually moves.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from .text import is_bitmap, text_box

Align = Literal["start", "center", "end"]
RGB = tuple[int, int, int]
Placed = tuple[Image.Image, int, int]

_CACHE_SIZE = 512
_cache: OrderedDict[Hashable, Image.Image] = OrderedDict()


def _cache_get(key: Hashable) -> Image.Image | None:
    img = _cache.get(key)
    if img is not None:
        _cache.move_to_end(key)
    return img


def _cache_put(key: Hashable, img: Image.Image) -> Image.Image:
    _cache[key] = img
    if len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)
    return img


def clear_cache() -> None:
    _cache.clear()


def _align(offset_space: int, align: Align) -> int:
    if align == "center":
        return offset_space // 2
    if align == "end":
        return offset_space
    return 0


class Node:
    """Base node. Subclasses implement measure() and place()."""

    @property
    def is_static(self) -> bool:
        """True when the rendered image does not depend on ``t``."""
        return True

    def cache_key(self) -> Hashable:
        """Structural identity used to cache static renders. None = uncacheable."""
        return None

    def measure(self) -> tuple[int, int]:
        raise NotImplementedError

    def place(self, x: int, y: int, w: int, h: int, t: float = 0.0) -> Iterator[Placed]:
        raise NotImplementedError


def render_node(node: Node, t: float = 0.0) -> Image.Image:
    """Render a node alone into an RGBA image of its natural size."""
    w, h = node.measure()
    return compose(node, max(w, 1), max(h, 1), t)


def compose(node: Node, w: int, h: int, t: float = 0.0) -> Image.Image:
    """Lay ``node`` out in a w x h box and composite into a transparent RGBA image."""
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for image, x, y in node.place(0, 0, w, h, t):
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        canvas.alpha_composite(image, (int(x), int(y)))
    return canvas


def render_tree(root: Node, width: int, height: int, background: RGB = (0, 0, 0), t: float = 0.0) -> Image.Image:
    """Lay out ``root`` to fill the canvas and composite into an RGB frame."""
    canvas = Image.new("RGBA", (width, height), (*background, 255))
    for image, x, y in root.place(0, 0, width, height, t):
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        canvas.alpha_composite(image, (int(x), int(y)))
    return canvas.convert("RGB")


# -- leaves ------------------------------------------------------------------


@dataclass
class Spacer(Node):
    """Flexible gap; ``weight`` shares leftover space among spacers."""

    weight: int = 1
    min: int = 0

    def cache_key(self):
        return ("spacer", self.weight, self.min)

    def measure(self) -> tuple[int, int]:
        return (self.min, self.min)

    def place(self, x, y, w, h, t=0.0):
        return iter(())


@dataclass
class Img(Node):
    image: Image.Image
    _key: Hashable = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Content hash, not id(): transient images get their ids recycled, which
        # would let cached material from a different image leak through.
        self._key = ("img", self.image.size, self.image.mode, hash(self.image.tobytes()))

    def cache_key(self):
        return self._key

    def measure(self) -> tuple[int, int]:
        return self.image.size

    def place(self, x, y, w, h, t=0.0):
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

    def cache_key(self):
        return ("text", self.text, id(self.font), self.fill, self.antialias)

    def measure(self) -> tuple[int, int]:
        return self._size

    def _image(self) -> Image.Image:
        key = self.cache_key()
        cached = _cache_get(key)
        if cached is not None:
            return cached
        img = Image.new("RGBA", (max(self._size[0], 1), max(self._size[1], 1)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        if not self.antialias:
            draw.fontmode = "1"
        if is_bitmap(self.font):
            draw.text(self._origin, self.text, font=self.font, fill=self.fill)
        else:
            draw.text(self._origin, self.text, font=self.font, fill=self.fill, anchor="la")
        return _cache_put(key, img)

    def place(self, x, y, w, h, t=0.0):
        yield (self._image(), x + _align(w - self._size[0], "center"), y + _align(h - self._size[1], "center"))


@dataclass
class Box(Node):
    """Solid or empty rectangle with optional fixed size."""

    width: int = 0
    height: int = 0
    fill: tuple[int, int, int, int] | None = None

    def cache_key(self):
        return ("box", self.width, self.height, self.fill)

    def measure(self):
        return (self.width, self.height)

    def place(self, x, y, w, h, t=0.0):
        if self.fill:
            yield (Image.new("RGBA", (max(w, 1), max(h, 1)), self.fill), x, y)


# -- containers --------------------------------------------------------------


class Container(Node):
    children: Sequence[Node]

    @property
    def is_static(self) -> bool:
        return all(c.is_static for c in self.children)

    def _children_key(self):
        keys = tuple(c.cache_key() for c in self.children)
        return None if any(k is None for k in keys) else keys

    def _layout(self, x: int, y: int, w: int, h: int, t: float) -> Iterator[Placed]:
        raise NotImplementedError

    def place(self, x, y, w, h, t=0.0):
        """Static subtrees are composited once and cached by (structure, size)."""
        key = self.cache_key()
        if key is None or not self.is_static:
            yield from self._layout(x, y, w, h, t)
            return
        full = (key, w, h)
        img = _cache_get(full)
        if img is None:
            canvas = Image.new("RGBA", (max(w, 1), max(h, 1)), (0, 0, 0, 0))
            for image, cx, cy in self._layout(0, 0, w, h, t):
                if image.mode != "RGBA":
                    image = image.convert("RGBA")
                canvas.alpha_composite(image, (int(cx), int(cy)))
            img = _cache_put(full, canvas)
        yield (img, x, y)


@dataclass
class _Linear(Container):
    children: Sequence[Node]
    spacing: int = 0
    align: Align = "center"
    horizontal: bool = True

    def cache_key(self):
        ck = self._children_key()
        return None if ck is None else ("linear", self.horizontal, self.spacing, self.align, ck)

    def _sizes(self) -> list[tuple[int, int]]:
        return [c.measure() for c in self.children]

    def measure(self) -> tuple[int, int]:
        sizes = self._sizes()
        if not sizes:
            return (0, 0)
        main = sum(s[0] if self.horizontal else s[1] for s in sizes) + self.spacing * (len(sizes) - 1)
        cross = max(s[1] if self.horizontal else s[0] for s in sizes)
        return (main, cross) if self.horizontal else (cross, main)

    def _layout(self, x, y, w, h, t):
        sizes = self._sizes()
        total_main = w if self.horizontal else h
        used = sum(s[0] if self.horizontal else s[1] for s in sizes) + self.spacing * max(len(sizes) - 1, 0)
        spacers = [c for c in self.children if isinstance(c, Spacer)]
        extra = max(total_main - used, 0)
        weight_total = sum(s.weight for s in spacers) or 0
        cursor = 0 if weight_total else extra // 2      # no spacers: centre along the main axis
        for child, (cw, ch) in zip(self.children, sizes):
            main = cw if self.horizontal else ch
            if isinstance(child, Spacer) and weight_total:
                main += extra * child.weight // weight_total
            stretch = isinstance(child, Container)       # containers fill the cross axis
            if self.horizontal:
                cross = h if stretch else ch
                yield from child.place(x + cursor, y + _align(h - cross, self.align), main, cross, t)
            else:
                cross = w if stretch else cw
                yield from child.place(x + _align(w - cross, self.align), y + cursor, cross, main, t)
            cursor += main + self.spacing


def HBox(children: Sequence[Node], spacing: int = 0, align: Align = "center") -> _Linear:
    return _Linear(list(children), spacing, align, horizontal=True)


def VBox(children: Sequence[Node], spacing: int = 0, align: Align = "center") -> _Linear:
    return _Linear(list(children), spacing, align, horizontal=False)


@dataclass
class Stack(Container):
    """Children drawn on top of each other, each centered in the box."""

    children: Sequence[Node]

    def cache_key(self):
        ck = self._children_key()
        return None if ck is None else ("stack", ck)

    def measure(self):
        sizes = [c.measure() for c in self.children] or [(0, 0)]
        return (max(s[0] for s in sizes), max(s[1] for s in sizes))

    def _layout(self, x, y, w, h, t):
        for child in self.children:
            yield from child.place(x, y, w, h, t)


@dataclass
class Absolute(Container):
    """Children at fixed rects: ``[(node, x, y, w, h), ...]`` — for pixel-exact designs."""

    items: Sequence[tuple[Node, int, int, int, int]]

    @property
    def children(self):
        return [i[0] for i in self.items]

    def cache_key(self):
        ck = self._children_key()
        return None if ck is None else ("absolute", tuple(i[1:] for i in self.items), ck)

    def measure(self):
        if not self.items:
            return (0, 0)
        return (max(x + w for _, x, y, w, h in self.items), max(y + h for _, x, y, w, h in self.items))

    def _layout(self, x, y, w, h, t):
        for node, cx, cy, cw, ch in self.items:
            yield from node.place(x + cx, y + cy, cw, ch, t)


@dataclass
class Anchor(Container):
    """Pin a child to a position inside the box (e.g. top-left, bottom-right)."""

    child: Node
    h: Align = "center"
    v: Align = "center"
    dx: int = 0
    dy: int = 0

    @property
    def children(self):
        return (self.child,)

    def cache_key(self):
        ck = self.child.cache_key()
        return None if ck is None else ("anchor", self.h, self.v, self.dx, self.dy, ck)

    def measure(self):
        return self.child.measure()

    def _layout(self, x, y, w, h, t):
        cw, ch = self.child.measure()
        yield from self.child.place(x + _align(w - cw, self.h) + self.dx, y + _align(h - ch, self.v) + self.dy, cw, ch, t)
