"""Holiday countdown board: image on the left (when we have one), big day count and name on the right."""
from __future__ import annotations

from functools import lru_cache

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext
from ...render import Absolute, Img, Sheen, Slide, Text, VBox, fit_font, load_font, render_tree
from ...render.anim import quintic_out
from .source import IMAGES

NUMBER = (80, 200, 255)
LABEL = (160, 170, 180)
NAME = (255, 255, 255)
TODAY_LABEL = (255, 160, 40)
TODAY_NAME = (255, 220, 100)
SEPARATOR = (40, 50, 60, 255)


class CountdownConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Holiday countdown")
    seconds_per_holiday: float = Field(5.0, ge=2, le=30)
    max_holidays: int = Field(3, ge=1, le=10, description="How many upcoming holidays to cycle through")


@lru_cache(maxsize=32)
def _image(name: str, size: int) -> Image.Image:
    img = Image.open(IMAGES / name).convert("RGBA")
    img.thumbnail((size, size), Image.LANCZOS)
    return img


def _wrap(text: str, font, width: int) -> list[str]:
    from ...render.text import text_size
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if text_size(trial, font)[0] <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:2]


class CountdownBoard(BaseBoard):
    key = "holidays.countdown"
    title = "Holiday countdown"
    config_model = CountdownConfig
    requires = frozenset({"holidays.upcoming"})

    def __init__(self) -> None:
        self._items: list[dict] = []

    def enter(self, ctx: BoardContext, cfg: CountdownConfig) -> None:
        self._items = list(ctx.snapshot.get("holidays.upcoming") or [])[: cfg.max_holidays]

    def done(self, ctx: BoardContext, cfg: CountdownConfig) -> bool:
        return ctx.elapsed >= cfg.seconds_per_holiday * max(len(self._items), 1)

    def render(self, ctx: BoardContext, cfg: CountdownConfig) -> Image.Image:
        if not self._items:
            self.enter(ctx, cfg)
        w, h = ctx.width, ctx.height
        if not self._items:
            return render_tree(Text("NO UPCOMING HOLIDAYS", load_font("pl", 6), LABEL), w, h)
        idx = min(int(ctx.elapsed // cfg.seconds_per_holiday), len(self._items) - 1)
        local = ctx.elapsed - idx * cfg.seconds_per_holiday
        item = self._items[idx]
        items = []
        text_x, text_w = 0, w
        if item.get("image") and w >= 96:
            img = _image(item["image"], h)
            items.append((Slide(Img(img), 0.5, "left", easing=quintic_out), 0, 0, img.width, h))
            sep_x = img.width + 2
            items.append((Img(Image.new("RGBA", (1, h - 8), SEPARATOR)), sep_x, 4, 1, h - 8))
            text_x, text_w = sep_x + 3, w - sep_x - 3
        big, small = load_font("pl", 12), load_font("pl", 6)
        today = item["days"] == 0
        if today:
            rows = [Text("TODAY IS", small, TODAY_LABEL)]
            rows += [Text(line, fit_font(line, "pl", text_w, 12), TODAY_NAME) for line in _wrap(item["name"].upper(), big, text_w)]
        else:
            rows = [Sheen(Text(str(item["days"]), big, NUMBER), period=3.0, band=10, strength=0.6, once=True, delay=0.6),
                    Text("DAY TIL" if item["days"] == 1 else "DAYS TIL", small, LABEL)]
            rows += [Text(line, small, NAME) for line in _wrap(item["name"].upper(), small, text_w)]
        col = VBox(rows, spacing=1)
        items.append((Slide(col, 0.4, "up", delay=0.1, easing=quintic_out), text_x, 0, text_w, h))
        return render_tree(Absolute(items), w, h, t=local)
