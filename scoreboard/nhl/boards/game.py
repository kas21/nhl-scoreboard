"""The main game board — a faithful port of the old client's 128x64 XL scoreboards.

Layers (bottom -> top): logos, centre gradient, teams-info / centre, indicators.
All geometry is from the old size profile; entrances are box-local wipes.
"""
from __future__ import annotations

from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext
from ...render import Absolute, Anchor, Box, HBox, Img, Sheen, Slide, Text, load_font, render_tree
from ...render.anim import cubic_out, elastic_out, exponential_out, quartic_out
from ...render.fx import Chip, fit_logo, reflected_gradient
from ..teams import logo, team
from .common import WHITE, fmt_date, fmt_time, local_time

BLACK = (0, 0, 0)
RED = (200, 0, 0)
GREEN = (0, 255, 0)

# 128x64 geometry (old p_128x64 profile)
LOGO_W, LOGO_H = 55, 45
LOGO_Y = {"pregame": 7, "live": 9, "intermission": 9, "postgame": 9}
HOME_LOGO_X = 73
HOME_STAGGER = 0.4          # seconds the home logo trails the away logo (slide + sheen)
GRADIENT = (34, 0, 60, 64)
SCORE_AWAY_X, SCORE_HOME_X, SCORE_Y = 53, 68, 25
HYPHEN = (62, 30, 4, 2)
SOG_Y = 43


class GameConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Game board")
    show_sog: bool = Field(True, description="Show shots on goal")
    show_records: bool = Field(True, description="Show team records before/after the game")
    time_24h: bool = False


class GameBoard(BaseBoard):
    key = "nhl.game"
    title = "NHL game"
    config_model = GameConfig
    requires = frozenset({"main_event"})

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}      # indicator key -> board time it appeared (for entrance replays)

    def enter(self, ctx: BoardContext, cfg: GameConfig) -> None:
        self._seen = {}

    # -- helpers -------------------------------------------------------------

    def _since(self, key: str, present: bool, now: float) -> float | None:
        """Board-time at which ``key`` became present (None when absent)."""
        if not present:
            self._seen.pop(key, None)
            return None
        return self._seen.setdefault(key, now)

    @staticmethod
    def _logo_node(abbrev: str, from_dir: str, delay: float = 0.0) -> Slide:
        """Logo wipes in (1.5s), then a single diagonal sheen; ``delay`` staggers the two sides."""
        img = fit_logo(logo(abbrev, 128), LOGO_W, LOGO_H)
        node = Sheen(Img(img), period=2.0, band=30, strength=0.6, once=True, delay=1.5 + delay, reverse=True)
        return Slide(node, duration=1.5, direction=from_dir, delay=delay, easing=exponential_out)

    @staticmethod
    def _score(value: int) -> Slide:
        return Slide(Text(str(value), load_font("score", 15), WHITE), duration=1.0, direction="up", easing=elastic_out)

    @staticmethod
    def _chip(text: str, abbrev: str, font=None):
        t = team(abbrev)
        return Chip(text, font or load_font("pl", 6), t.text_on_primary, t.primary)

    def _sog_row(self, g: dict[str, Any], f6) -> list:
        return [
            (Anchor(Text(str(g["away"]["sog"]), f6, WHITE), h="end"), 42, SOG_Y, 14, 5),
            (Chip("SOG", f6, BLACK, WHITE), 58, SOG_Y - 1, 13, 7),
            (Anchor(Text(str(g["home"]["sog"]), f6, WHITE), h="start"), 73, SOG_Y, 14, 5),
        ]

    # -- render ---------------------------------------------------------------

    def render(self, ctx: BoardContext, cfg: GameConfig) -> Image.Image:
        g = ctx.snapshot.get("main_event")
        if not g:
            return Image.new("RGB", (ctx.width, ctx.height))
        phase = g["phase"]
        ly = LOGO_Y.get(phase, 9)
        items: list = [
            (self._logo_node(g["away"]["abbrev"], "left"), 0, ly, LOGO_W, LOGO_H),
            (self._logo_node(g["home"]["abbrev"], "right", delay=HOME_STAGGER), HOME_LOGO_X, ly, LOGO_W, LOGO_H),
            (Img(reflected_gradient(GRADIENT[2], GRADIENT[3])), GRADIENT[0], GRADIENT[1], GRADIENT[2], GRADIENT[3]),
        ]
        if phase == "pregame":
            items += self._pregame(g, ctx, cfg)
        elif phase == "postgame":
            items += self._final(g, ctx, cfg)
        else:
            items += self._live(g, ctx, cfg)
        tree = Absolute(items)
        return render_tree(tree, ctx.width, ctx.height, t=ctx.elapsed)

    def _teams_info(self, g: dict[str, Any], cfg: GameConfig) -> list:
        f6, f8 = load_font("pl", 6), load_font("block", 8)
        stroke = (0, 0, 0, 220)
        ta, th = team(g["away"]["abbrev"]), team(g["home"]["abbrev"])
        items = [
            (Slide(Chip(g["away"]["abbrev"], f8, ta.text_on_primary, ta.primary, stroke=stroke), 0.8, "left", easing=exponential_out, h_align="start"), 2, 45, 25, 11),
            (Slide(Chip(g["home"]["abbrev"], f8, th.text_on_primary, th.primary, stroke=stroke), 0.8, "right", easing=exponential_out, h_align="end"), 101, 45, 25, 11),
        ]
        if cfg.show_records:
            items += [
                (Slide(Text(g["away"]["record"], f6, WHITE), 0.5, "up", easing=cubic_out, h_align="start"), 3, 57, 40, 5),
                (Slide(Text(g["home"]["record"], f6, WHITE), 0.5, "up", easing=cubic_out, h_align="end"), 85, 57, 40, 5),
            ]
        return items

    def _pregame(self, g, ctx, cfg) -> list:
        f6 = load_font("pl", 6)
        start = local_time(g["start_time_utc"], ctx.now.tzinfo)
        date = fmt_date(g["date"]).replace(" ", "")
        return self._teams_info(g, cfg) + [
            (Chip(date, f6, BLACK, WHITE), 39, 14, 50, 7),
            (Text(fmt_time(start, cfg.time_24h).upper() or "TBD", f6, WHITE), 39, 22, 50, 5),
            (Text("VS", load_font("score", 15), WHITE), 39, 31, 50, 12),
        ]

    def _final(self, g, ctx, cfg) -> list:
        f6 = load_font("pl", 6)
        label = g["outcome"].replace("FINAL/", "FINAL/") if g["outcome"] else "FINAL"
        items = self._teams_info(g, cfg) + [
            (Chip(label, f6, WHITE, RED), 34, 14, 60, 7),
            (self._score(g["away"]["score"]), SCORE_AWAY_X, SCORE_Y, 8, 12),
            (Box(fill=(255, 255, 255, 255)), *HYPHEN),
            (self._score(g["home"]["score"]), SCORE_HOME_X - 1, SCORE_Y, 8, 12),
        ]
        if cfg.show_sog:
            items += self._sog_row(g, f6)
        return items

    def _live(self, g, ctx, cfg) -> list:
        f6 = load_font("pl", 6)
        t = ctx.elapsed
        period = "INT" if g["in_intermission"] else g["period"].upper()
        strip = HBox([Chip(period, f6, BLACK, WHITE), Text(g["clock"], f6, WHITE)], spacing=1)
        items = [
            (strip, 34, 14, 60, 7),
            (self._score(g["away"]["score"]), SCORE_AWAY_X, SCORE_Y, 8, 12),
            (Box(fill=(255, 255, 255, 255)), *HYPHEN),
            (self._score(g["home"]["score"]), SCORE_HOME_X, SCORE_Y, 8, 12),
        ]
        if cfg.show_sog:
            items += self._sog_row(g, f6)
        # -- indicators (each replays its entrance when it appears) --
        code = g["powerplay"]["code"]
        pp_side = None if code == "ev" else ("away" if code[0] == "a" else "home")
        for side, x, align in (("away", 0, "start"), ("home", 82, "end")):
            since = self._since(f"pp:{side}", pp_side == side, t)
            if since is not None:
                label = f"PP {code[1]}-{code[2]}"
                node = HBox([self._chip(label, g[side]["abbrev"]), Text(g["powerplay"]["clock"], f6, WHITE)], spacing=1)
                items.append((Slide(node, 0.6, "up", delay=since, easing=quartic_out, h_align=align), x, 0, 45, 7))
            en = self._since(f"en:{side}", bool(g["pulled_goalie"] & (1 if side == "away" else 2)), t)
            if en is not None:
                node = self._chip("EMPTY NET", g[side]["abbrev"])
                items.append((Slide(node, 0.6, "down", delay=en, easing=quartic_out, h_align=align), x if side == "away" else 91, 57, 37, 7))
        inter = self._since("int", g["in_intermission"], t)
        if inter is not None:
            items.append((Slide(Box(36, 3, (*GREEN, 255)), 0.3, "down", delay=inter, easing=elastic_out), 46, 0, 36, 3))
        return items
