"""Drawing helpers shared by boards: gradients, chips, outlined logos."""
from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .anim import quintic_out
from .layout import Img, Node, Text
from .text import is_bitmap, text_box

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]


@lru_cache(maxsize=32)
def reflected_gradient(width: int, height: int, horizontal: bool = False, color: RGB = (0, 0, 0)) -> Image.Image:
    """Black (or ``color``) with alpha ramping quintic-out to opaque in the middle, transparent at both edges.

    Used behind the centre column of the scoreboards to darken the logos where text sits.
    ``horizontal=False`` ramps per column (left/right edges transparent); ``True`` ramps per row.
    """
    img = Image.new("RGBA", (width, height), (*color, 0))
    px = img.load()
    span = width if not horizontal else height
    half = round(span / 2) + 1
    ramp = [int(255 * quintic_out(i / max(half - 1, 1))) for i in range(half)]
    for i in range(span):
        a = ramp[i] if i < half else ramp[max(span - 1 - i, 0)]
        if not horizontal:
            for y in range(height):
                px[i, y] = (*color, a)
        else:
            for x in range(width):
                px[x, i] = (*color, a)
    return img


def chip(text: str, font: ImageFont.ImageFont, fg: RGB, bg: RGB, pad: tuple[int, int, int, int] = (1, 1, 1, 1),
         stroke: RGBA | None = None) -> Image.Image:
    """Text on a solid box with (left, top, right, bottom) padding and an optional 1px outline ring."""
    left, top, right, bottom = text_box(text, font)
    w, h = right - left, bottom - top
    pl, pt, pr, pb = pad
    box = Image.new("RGBA", (w + pl + pr, h + pt + pb), (*bg, 255))
    draw = ImageDraw.Draw(box)
    draw.fontmode = "1"
    if is_bitmap(font):
        draw.text((pl - left, pt - top), text, font=font, fill=fg)
    else:
        draw.text((pl - left, pt - top), text, font=font, fill=fg, anchor="la")
    if stroke is None:
        return box
    ring = Image.new("RGBA", (box.width + 2, box.height + 2), stroke)
    ring.alpha_composite(box, (1, 1))
    return ring


def Chip(text: str, font: ImageFont.ImageFont, fg: RGB, bg: RGB, pad=(1, 1, 1, 1), stroke: RGBA | None = None) -> Node:
    return Img(chip(text, font, fg, bg, pad, stroke))


def outlined(img: Image.Image, color: RGBA = (0, 0, 0, 255), radius: int = 2) -> Image.Image:
    """Add a solid outline ring around an RGBA image's opaque pixels (dilated alpha)."""
    alpha = img.getchannel("A").filter(ImageFilter.MaxFilter(radius * 2 + 1))
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(Image.new("RGBA", img.size, color), (0, 0), alpha)
    out.alpha_composite(img)
    return out


def stroked_text(text: str, font: ImageFont.ImageFont, fill: RGB, stroke: RGB, width: int = 2, pad: int = 4) -> Image.Image:
    """Large display text with a stroke outline (antialiasing off)."""
    left, top, right, bottom = font.getbbox(text, anchor="la", stroke_width=width)
    img = Image.new("RGBA", (right - left + 2 * pad, bottom - top + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    d.text((pad - left, pad - top), text, font=font, fill=fill, stroke_width=width, stroke_fill=stroke, anchor="la")
    return img


def fit_logo(img: Image.Image, width: int, height: int) -> Image.Image:
    """Aspect-fit a logo into a transparent width x height canvas, centred (the old client's logo box)."""
    scaled = img.copy()
    scaled.thumbnail((width, height), Image.LANCZOS)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(scaled, ((width - scaled.width) // 2, (height - scaled.height) // 2))
    return canvas


__all__ = ["Chip", "Text", "chip", "fit_logo", "outlined", "reflected_gradient", "stroked_text"]
