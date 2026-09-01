"""Boot splash — port of the old client's: logo centred, slides left, words bounce in, sheen loops,
flavour text scrolls along the bottom, V2 badge top-right."""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, Field

from ..render import Absolute, Img, Sheen, Slide, Text, load_font, render_tree
from ..render.anim import bounce_out, quintic_in_out
from ..render.fx import chip
from .base import BaseBoard, BoardContext

ASSETS = Path(__file__).parent.parent / "assets"
GREY = (120, 120, 120)
ORANGE = (255, 165, 0)
FLAVOUR = [
    "Reviewing for a distinct kicking motion", "Icing waved off", "Too many men on the ice",
    "Goalie interference under review", "Offside challenge coming", "Warming up the Zamboni",
    "Sharpening skates", "Taping sticks", "Tuning the goal horn", "Checking the standings",
    "Waiting for puck drop", "Pulling the goalie", "Shootout ready", "Overtime loading",
]


class SplashConfig(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}
    words: str = Field("NHL LED SCOREBOARD", max_length=40)
    badge: str = Field("V2", max_length=4)


class SplashBoard(BaseBoard):
    key = "splash"
    title = "Splash"
    config_model = SplashConfig

    def __init__(self) -> None:
        self._flavour: str = random.choice(FLAVOUR)
        self._flavour_started = 0.0

    def enter(self, ctx: BoardContext, cfg: SplashConfig) -> None:
        self._flavour = random.choice(FLAVOUR)
        self._flavour_started = 5.5

    def render(self, ctx: BoardContext, cfg: SplashConfig) -> Image.Image:
        t = ctx.elapsed
        w, h = ctx.width, ctx.height
        logo = Image.open(ASSETS / "splash.png").convert("RGBA")
        lx_center, lx_left = (w - logo.width) // 2, 2
        if t < 2.0:
            lx = lx_center
        elif t < 4.0:
            lx = int(lx_center + (lx_left - lx_center) * quintic_in_out((t - 2.0) / 2.0))
        else:
            lx = lx_left
        ly = int(h * 0.46) - logo.height // 2
        items = [(Img(logo), lx, ly, logo.width, logo.height)]
        f7, f6 = load_font("camels", 7), ctx.profile.label_font()
        words = cfg.words.split()
        if t >= 4.0 and words:
            tx = lx_left + logo.width + 3
            base_y = h // 2 - 6
            first, second = words[:-1] or words, words[-1:] if len(words) > 1 else []
            x = tx
            row = []
            for wd in first:
                node = Text(wd, f7, GREY)
                ww = node.measure()[0]
                row.append((node, x, base_y - 3, ww, 7))
                x += ww + 4
            if second:
                node = Text(second[0], f7, GREY)
                row.append((node, tx, base_y + 5, node.measure()[0], 7))
            done_at = 4.0 + 1.03
            if t < done_at:
                for i, (node, x, y, ww, hh) in enumerate(row):
                    direction = "down" if (second and i == len(row) - 1) else "up"
                    items.append((Slide(node, 1.03, direction, delay=4.0, easing=bounce_out, h_align="start"), x, y, ww, hh))
            else:
                for node, x, y, ww, hh in row:
                    items.append((Sheen(node, period=6.0, band=12, strength=0.8, delay=done_at, h_align="start"), x, y, ww, hh))
        if t >= self._flavour_started:
            text = Text(self._flavour, f6, ORANGE)
            tw = text.measure()[0]
            x = int(w - (t - self._flavour_started) * 40)
            if x < -tw:                                  # fully off: pick another line
                self._flavour = random.choice(FLAVOUR)
                self._flavour_started = t
                x = w
            items.append((text, x, h - 6, tw, 6))
        if cfg.badge:
            b = chip(cfg.badge, f6, (255, 255, 255), (255, 0, 0), pad=(2, 2, 2, 2))
            items.append((Img(b), w - 2 - b.width, 0, b.width, b.height))
        return render_tree(Absolute(items), w, h, t=t)
