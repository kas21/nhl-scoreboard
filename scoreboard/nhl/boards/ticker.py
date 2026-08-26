"""Score ticker — port of the old ScoretickerXL card: logos stacked left (away over home),
names/records/scores right, date chip + time (scheduled) or period/clock (live/final).
Each card's elements slide in; the board reports done after every game has shown."""
from __future__ import annotations

from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext
from ...render import Absolute, HBox, Img, Sheen, Slide, Text, load_font, render_tree
from ...render.anim import exponential_in_out, linear
from ...render.fx import Chip, chip, fit_logo
from ..teams import logo
from .common import fmt_date, fmt_time, local_time

WHITE = (255, 255, 255)
LIGHT = (200, 200, 200)
RED = (200, 0, 0)
BLACK = (0, 0, 0)


def _wrap(text: str, font, width: int) -> list[str]:
    """Word-wrap onto up to two lines that fit ``width`` (the old board's 80px name wrap)."""
    from ...render.text import text_size
    if text_size(text, font)[0] <= width:
        return [text]
    words = text.split()
    for i in range(len(words) - 1, 0, -1):
        first, rest = " ".join(words[:i]), " ".join(words[i:])
        if text_size(first, font)[0] <= width and text_size(rest, font)[0] <= width:
            return [first, rest]
    return [text]


class TickerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Score ticker")
    seconds_per_game: float = Field(8.0, ge=2, le=30)
    time_24h: bool = False
    skip_finished: bool = Field(False, description="Only show upcoming and live games")


class TickerBoard(BaseBoard):
    key = "nhl.ticker"
    title = "Score ticker"
    config_model = TickerConfig
    requires = frozenset({"nhl.scores"})

    def __init__(self) -> None:
        self._games: list[dict[str, Any]] = []

    def enter(self, ctx: BoardContext, cfg: TickerConfig) -> None:
        games = ctx.snapshot.get("nhl.scores") or []
        if cfg.skip_finished:
            games = [g for g in games if g["phase"] != "postgame"] or games
        self._games = list(games)

    def done(self, ctx: BoardContext, cfg: TickerConfig) -> bool:
        return ctx.elapsed >= cfg.seconds_per_game * max(len(self._games), 1)

    def render(self, ctx: BoardContext, cfg: TickerConfig) -> Image.Image:
        if not self._games:
            self.enter(ctx, cfg)
        w, h = ctx.width, ctx.height
        if not self._games:
            return render_tree(Text("NO GAMES TODAY", load_font("pl", 6), LIGHT), w, h)
        idx = min(int(ctx.elapsed // cfg.seconds_per_game), len(self._games) - 1)
        local = ctx.elapsed - idx * cfg.seconds_per_game
        return render_tree(Absolute(self._card(self._games[idx], ctx, cfg)), w, h, t=local)

    def _card(self, g: dict[str, Any], ctx: BoardContext, cfg: TickerConfig) -> list:
        f7, f6 = load_font("camels", 7), load_font("pl", 6)
        half = ctx.height // 2
        logo_w, win_h = 45, half - 1
        items = []
        for side, y, direction, delay, sheen_delay in (("away", 0, "down", 0.0, 0.0), ("home", half + 1, "up", 0.3, 1.4)):
            img = fit_logo(logo(g[side]["abbrev"], 128), logo_w, win_h + 4)
            node = Sheen(Img(img), period=2.0, band=25, strength=0.6, once=True, delay=1.0 + sheen_delay)
            items.append((Slide(node, 1.0, direction, delay=delay, easing=exponential_in_out), 0, y, logo_w, win_h))
        pregame = g["phase"] == "pregame"
        name_w = ctx.width - 48 - (0 if pregame else 20)
        for side, top in (("away", 0), ("home", half + 1)):
            name = (g[side]["name"] if pregame else g[side]["abbrev"]).upper()
            lines = _wrap(name, f7, name_w)
            y0 = top + 9 if len(lines) == 1 else top + 3
            for i, line in enumerate(lines[:2]):
                items.append((Slide(Text(line, f7, WHITE), 0.4, "up", easing=linear, h_align="start"), 48, y0 + 8 * i, name_w, 8))
            if pregame:
                items.append((Slide(Text(g[side]["record"] or "0-0-0", f6, LIGHT), 0.4, "up", easing=linear, h_align="start"), 48, top + 21, 40, 5))
            else:
                items.append((Slide(Text(str(g[side]["score"]), f7, WHITE), 0.4, "up", easing=linear, h_align="end"), 111, top + 7, 16, 12))
        if pregame:
            date = chip(fmt_date(g["date"]).replace(" ", ""), f6, BLACK, WHITE)
            # date chip + start time sit in the seam between the halves, clear of both name blocks
            items.append((Slide(Img(date), 0.4, "right", easing=linear, h_align="end"), 100, half - 8, 27, 7))
            start = local_time(g["start_time_utc"], ctx.now.tzinfo)
            items.append((Slide(Text(fmt_time(start, cfg.time_24h).upper(), f6, WHITE), 0.4, "right", easing=linear, h_align="end"), 90, half + 2, 37, 5))
        elif g["phase"] == "postgame":
            label = g["outcome"].replace("FINAL/", "F/") if "/" in g["outcome"] else "FINAL"
            items.append((Slide(Chip(label, f6, WHITE, RED), 0.4, "right", easing=linear, h_align="end"), 67, half - 4, 60, 8))
        else:
            period = "INT" if g["in_intermission"] else g["period"].upper()
            strip = HBox([Chip(period, f6, BLACK, WHITE), Text(g["clock"], f6, WHITE)], spacing=1)
            items.append((Slide(strip, 0.4, "right", easing=linear, h_align="end"), 67, half - 4, 60, 8))
        return items
