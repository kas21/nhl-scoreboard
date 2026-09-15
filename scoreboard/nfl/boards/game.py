"""NFL game board: the NHL XL layout with football specifics — quarter/clock, down & distance,
the spot of the ball, a scrolling last play, possession and red-zone chips, timeouts."""
from __future__ import annotations

from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...nhl.boards.game import GameBoard as NhlGameBoard
from ...render import Box, HBox, Marquee, Slide, Text
from ...render.anim import quartic_out
from ..teams import logo, text_on

WHITE = (255, 255, 255)
GREY = (190, 190, 190)
RED = (200, 0, 0)
YELLOW = (255, 200, 0)

# 128x64 live-layout rows below the score (x, y, w, h); the logos end at y=54, timeout dots sit at y=61
DOWN_ROW = (34, 43, 60, 6)      # "3RD & 7", where the NHL board shows SOG
SPOT_ROW = (34, 50, 60, 5)      # "KC 44" — the spot of the ball
PLAY_STRIP = (12, 56, 104, 5)   # last play, marquees when wider than the strip
PLAY_SPEED = 20.0               # px/s; an 80-char play (~320 px) laps in about 16 s
PLAY_PAUSE = 1.5                # seconds the play holds before it starts scrolling
MAX_DOWN_CHARS = 14


class NflGameConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="NFL game board")
    show_records: bool = True
    show_down_distance: bool = True
    show_field_position: bool = Field(True, description="Show where the ball is (e.g. KC 44) under the down & distance")
    show_last_play: bool = Field(True, description="Scroll the last play's description along the bottom")
    show_timeouts: bool = True
    time_24h: bool = False
    show_sog: bool = False        # unused for football; kept so the shared layout code can read it


class NflGameBoard(NhlGameBoard):
    key = "nfl.game"
    title = "NFL game"
    config_model = NflGameConfig
    sport = "nfl"

    def logo_image(self, abbrev: str, g: dict[str, Any]) -> Image.Image:
        return logo(abbrev, 128)

    def side_colors(self, g: dict[str, Any], side: str):
        primary = tuple(g[side].get("color") or (90, 90, 90))
        return primary, text_on(primary)

    def _live_stats_row(self, g: dict[str, Any], cfg, f6) -> list:
        """Down & distance where the NHL board shows SOG, the spot under it, the last play along the bottom."""
        sit = g.get("situation") or {}
        return self._down_row(sit, cfg, f6) + self._spot_row(sit, cfg, f6) + self._play_strip(sit, cfg, f6)

    @staticmethod
    def _down_row(sit: dict[str, Any], cfg, f6) -> list:
        text = (sit.get("text") or "").upper()
        if not text or not getattr(cfg, "show_down_distance", True):
            return []
        return [(Text(text[:MAX_DOWN_CHARS], f6, YELLOW if sit.get("red_zone") else WHITE), *DOWN_ROW)]

    @staticmethod
    def _spot_row(sit: dict[str, Any], cfg, f6) -> list:
        spot = (sit.get("spot") or "").upper()
        if not spot or not getattr(cfg, "show_field_position", True):
            return []
        return [(Text(spot, f6, GREY), *SPOT_ROW)]

    @staticmethod
    def _play_strip(sit: dict[str, Any], cfg, f6) -> list:
        play = (sit.get("last_play") or "").upper()
        if not play or not getattr(cfg, "show_last_play", True):
            return []
        node = Marquee(Text(play, f6, GREY), width=PLAY_STRIP[2], speed=PLAY_SPEED, pause=PLAY_PAUSE)
        return [(node, *PLAY_STRIP)]

    def _final_stats_row(self, g: dict[str, Any], cfg, f6) -> list:
        return []

    def _indicators(self, g: dict[str, Any], t: float, f6) -> list:
        """Possession chip top corner of the team with the ball; RED ZONE badge; timeout dots bottom corners."""
        items: list = []
        sit = g.get("situation") or {}
        poss = sit.get("possession")
        for side, x, align in (("away", 0, "start"), ("home", 82, "end")):
            since = self._since(f"poss:{side}", poss == side, t)
            if since is not None:
                label = "RED ZONE" if sit.get("red_zone") else "BALL"
                node = self._chip(label, g, side, f6)
                items.append((Slide(node, 0.6, "up", delay=since, easing=quartic_out, h_align=align), x, 0, 45, 7))
            to = g[side].get("timeouts")
            if to is not None:
                dots = HBox([Box(2, 2, (255, 255, 255, 255) if i < int(to) else (60, 60, 60, 255)) for i in range(3)], spacing=1)
                items.append((dots, 2 if side == "away" else 118, 61, 8, 2))
        half = self._since("half", g.get("in_intermission", False), t)
        if half is not None:
            items.append((Slide(Box(36, 3, (0, 255, 0, 255)), 0.3, "down", delay=half, easing=quartic_out), 46, 0, 36, 3))
        return items
