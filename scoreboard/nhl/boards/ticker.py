"""Score ticker: cycles through every game today, one at a time, then reports done."""
from __future__ import annotations

from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext
from ...render import HBox, Sequence, Spacer, Text, VBox, load_font, render_tree
from ...render.text import fit_font
from .common import DIM, GREY, WHITE, YELLOW, fmt_time, local_time, score_text, team_logo


class TickerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Score ticker")
    seconds_per_game: float = Field(6.0, ge=2, le=30)
    time_24h: bool = False
    skip_finished: bool = Field(False, description="Only show upcoming and live games")


class TickerBoard(BaseBoard):
    key = "nhl.ticker"
    title = "Score ticker"
    config_model = TickerConfig
    requires = frozenset({"nhl.scores"})

    def __init__(self) -> None:
        self._games: list[dict[str, Any]] = []
        self._cache: dict[int, Sequence] = {}
        self._size: tuple[int, int] = (0, 0)

    def enter(self, ctx: BoardContext, cfg: TickerConfig) -> None:
        games = ctx.snapshot.get("nhl.scores") or []
        if cfg.skip_finished:
            games = [g for g in games if g["phase"] != "postgame"] or games
        self._games = list(games)
        self._cache = {}
        self._size = (ctx.width, ctx.height)

    def _slot(self, ctx: BoardContext, cfg: TickerConfig) -> tuple[int, float]:
        idx = int(ctx.elapsed // cfg.seconds_per_game)
        return idx, ctx.elapsed - idx * cfg.seconds_per_game

    def render(self, ctx: BoardContext, cfg: TickerConfig) -> Image.Image:
        if not self._games or self._size != (ctx.width, ctx.height):
            self.enter(ctx, cfg)
        if not self._games:
            return render_tree(Text("NO GAMES TODAY", load_font("pixel", ctx.profile.font_medium), GREY), ctx.width, ctx.height)
        idx, within = self._slot(ctx, cfg)
        idx = min(idx, len(self._games) - 1)
        seq = self._cache.get(idx)
        if seq is None:
            still = render_tree(self._card(self._games[idx], ctx, cfg), ctx.width, ctx.height)
            seq = self._cache[idx] = Sequence(ctx.fps).slide_in("up", 0.35).hold(cfg.seconds_per_game).build(still)
        return seq.at(within)

    def done(self, ctx: BoardContext, cfg: TickerConfig) -> bool:
        return ctx.elapsed >= cfg.seconds_per_game * max(len(self._games), 1)

    def _card(self, g: dict[str, Any], ctx: BoardContext, cfg: TickerConfig):
        p = ctx.profile
        if g["phase"] == "pregame":
            start = local_time(g["start_time_utc"], ctx.now.tzinfo)
            mid = VBox([Text(fmt_time(start, cfg.time_24h) or "TBD", load_font("pixel", p.font_medium), WHITE),
                        Text("VS", load_font("pixel", p.font_small), DIM)], spacing=1)
            sides = (team_logo(g["away"]["abbrev"], p.logo), team_logo(g["home"]["abbrev"], p.logo))
        else:
            top = g["outcome"] if g["phase"] == "postgame" else ("INT" if g["in_intermission"] else g["clock"])
            sub = "" if g["phase"] == "postgame" else g["period"]
            color = YELLOW if g["phase"] == "postgame" or g["in_intermission"] else WHITE
            mid = VBox([Text(top, fit_font(top, "pixel", ctx.width // 3, p.font_medium), color),
                        Text(sub, load_font("pixel", p.font_small), GREY)] if sub else
                       [Text(top, fit_font(top, "pixel", ctx.width // 3, p.font_medium), color)], spacing=1)
            sides = (
                self._side(g, "away", p),
                self._side(g, "home", p),
            )
        return HBox([sides[0], Spacer(), mid, Spacer(), sides[1]], spacing=p.pad)

    def _side(self, g, side, p):
        parts = [team_logo(g[side]["abbrev"], p.logo_small), score_text(g[side]["score"], p)]
        if side == "home":
            parts.reverse()
        return VBox(parts) if p.width < 96 else HBox(parts, spacing=p.pad)
