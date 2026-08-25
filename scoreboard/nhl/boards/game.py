"""The main game board: pregame, live and final layouts chosen by phase."""
from __future__ import annotations

from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext
from ...render import HBox, Spacer, Text, VBox, load_font, render_tree
from ...render.text import fit_font
from .common import (
    DIM,
    GREY,
    RED,
    WHITE,
    YELLOW,
    fmt_date,
    fmt_time,
    gradient_backdrop,
    indicator,
    local_time,
    score_text,
    team_logo,
)

Side = Literal["away", "home"]


class GameConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Game board")
    show_sog: bool = Field(True, description="Show shots on goal during live games")
    show_records: bool = Field(True, description="Show team records before the game")
    gradient: bool = Field(True, description="Team-colour gradient background")
    time_24h: bool = False


class GameBoard(BaseBoard):
    key = "nhl.game"
    title = "NHL game"
    config_model = GameConfig
    requires = frozenset({"main_event"})

    def render(self, ctx: BoardContext, cfg: GameConfig) -> Image.Image:
        game = ctx.snapshot.get("main_event")
        if not game:
            return Image.new("RGB", (ctx.width, ctx.height))
        phase = game["phase"]
        if phase == "pregame":
            tree = self._pregame(game, ctx, cfg)
        elif phase == "postgame":
            tree = self._final(game, ctx, cfg)
        else:
            tree = self._live(game, ctx, cfg)
        frame = render_tree(tree, ctx.width, ctx.height)
        if cfg.gradient:
            bg = gradient_backdrop(ctx.width, ctx.height, game["away"]["abbrev"], game["home"]["abbrev"])
            mask = frame.convert("L").point(lambda v: 255 if v else 0)
            bg.paste(frame, (0, 0), mask)
            frame = bg
        return frame

    # -- layouts -------------------------------------------------------------

    def _pregame(self, g: dict[str, Any], ctx: BoardContext, cfg: GameConfig):
        p = ctx.profile
        start = local_time(g["start_time_utc"], ctx.now.tzinfo)
        when = fmt_time(start, cfg.time_24h)
        day = "TODAY" if start and start.date() == ctx.now.date() else (start.strftime("%a").upper() if start else fmt_date(g["date"]))
        center = VBox([
            Text(day, load_font("pixel", p.font_small), GREY),
            Text(when or "TBD", load_font("pixel", p.font_medium), WHITE),
            Text("VS", load_font("pixel", p.font_small), DIM),
        ], spacing=1)
        rows = [Spacer(), HBox([team_logo(g["away"]["abbrev"], p.logo), Spacer(), center, Spacer(), team_logo(g["home"]["abbrev"], p.logo)])]
        if cfg.show_records and p.show_records and (g["away"]["record"] or g["home"]["record"]):
            rows.append(HBox([Text(g["away"]["record"], load_font("pixel", p.font_small), GREY), Spacer(), Text(g["home"]["record"], load_font("pixel", p.font_small), GREY)]))
        rows.append(Spacer())
        return VBox(rows, spacing=p.pad)

    def _live(self, g: dict[str, Any], ctx: BoardContext, cfg: GameConfig):
        p = ctx.profile
        status = "INT" if g["in_intermission"] else g["clock"]
        score_w = score_text(88, p).measure()[0]
        sides = 2 * ((max(p.logo_small, score_w) if p.width < 96 else p.logo_small + score_w) + 3 * p.pad)
        clock_font = fit_font(status, "clock", max(ctx.width - sides, 16), p.font_medium)
        center = VBox([
            Text(g["period"], load_font("pixel", p.font_small), GREY),
            Text(status, clock_font, YELLOW if g["in_intermission"] else WHITE),
        ], spacing=1)
        rows = [Spacer(), HBox([
            self._side(g, "away", p, cfg), Spacer(), center, Spacer(), self._side(g, "home", p, cfg),
        ], spacing=1)]
        badges = self._badges(g, p)
        if badges:
            rows.append(HBox([Spacer(), *badges, Spacer()], spacing=2))
        rows.append(Spacer())
        return VBox(rows, spacing=p.pad)

    def _side(self, g: dict[str, Any], side: Side, p, cfg: GameConfig):
        t = g[side]
        parts = [team_logo(t["abbrev"], p.logo_small), score_text(t["score"], p)]
        if side == "home":
            parts.reverse()
        col = VBox(parts, spacing=0) if p.width < 96 else HBox(parts, spacing=p.pad)
        if cfg.show_sog and p.show_sog and g["phase"] != "postgame":
            return VBox([col, Text(f"SOG {t['sog']}", load_font("pixel", p.font_small), GREY)], spacing=1)
        return col

    def _badges(self, g: dict[str, Any], p) -> list:
        badges = []
        code = g["powerplay"]["code"]
        if code != "ev":
            side = "away" if code[0] == "a" else "home"
            label = f"{g[side]['abbrev']} PP {code[1]}-{code[2]}"
            if g["powerplay"]["clock"] and p.width >= 96:
                label += f" {g['powerplay']['clock']}"
            badges.append(indicator(label, p, YELLOW))
        if g["pulled_goalie"] & 1:
            badges.append(indicator(f"{g['away']['abbrev']} EN", p, RED, WHITE))
        if g["pulled_goalie"] & 2:
            badges.append(indicator(f"{g['home']['abbrev']} EN", p, RED, WHITE))
        return badges

    def _final(self, g: dict[str, Any], ctx: BoardContext, cfg: GameConfig):
        p = ctx.profile
        center = VBox([
            Text(g["outcome"] or "FINAL", fit_font(g["outcome"] or "FINAL", "pixel", ctx.width // 3, p.font_medium), YELLOW),
            Text(fmt_date(g["date"]), load_font("pixel", p.font_small), GREY),
        ], spacing=1)
        rows = [Spacer(), HBox([self._side(g, "away", p, cfg), Spacer(), center, Spacer(), self._side(g, "home", p, cfg)], spacing=1)]
        if cfg.show_sog and p.show_sog:
            rows.append(HBox([Text(f"SOG {g['away']['sog']}", load_font("pixel", p.font_small), DIM), Spacer(), Text(f"SOG {g['home']['sog']}", load_font("pixel", p.font_small), DIM)]))
        rows.append(Spacer())
        return VBox(rows, spacing=p.pad)
