"""Standings: a table that scrolls vertically, one division/group at a time."""
from __future__ import annotations

from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext
from ...render import HBox, Spacer, Text, VBox, load_font, render_tree
from ...render.layout import Box, Img
from ..teams import logo
from .common import GREY, WHITE, YELLOW


class StandingsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Standings")
    view: Literal["division", "wildcard", "league"] = "division"
    scroll_speed: float = Field(8.0, ge=2, le=40, description="Pixels per second")
    hold_seconds: float = Field(2.0, ge=0, le=10, description="Pause at top before scrolling")
    highlight: list[str] = Field([], description="Team abbrevs to highlight (defaults to favourites)")


class StandingsBoard(BaseBoard):
    key = "nhl.standings"
    title = "Standings"
    config_model = StandingsConfig
    requires = frozenset({"nhl.standings"})

    def __init__(self) -> None:
        self._strip: Image.Image | None = None
        self._size = (0, 0)

    def enter(self, ctx: BoardContext, cfg: StandingsConfig) -> None:
        self._strip = self._build(ctx, cfg)
        self._size = (ctx.width, ctx.height)

    def _groups(self, standings: dict[str, Any], cfg: StandingsConfig) -> list[tuple[str, list[str]]]:
        if cfg.view == "league":
            return [("NHL", standings["league"])]
        if cfg.view == "wildcard":
            return [(f"{conf[:4]} {grp}".upper(), teams) for conf, groups in standings["wildcard"].items() for grp, teams in groups.items()]
        return [(name.upper(), teams) for name, teams in standings["division"].items()]

    def _build(self, ctx: BoardContext, cfg: StandingsConfig) -> Image.Image:
        p = ctx.profile
        standings = ctx.snapshot.get("nhl.standings") or {}
        rows = standings.get("teams") or {}
        highlight = {h.upper() for h in cfg.highlight} or self._favorites(ctx)
        font = load_font("pixel", p.font_small)
        row_h = max(font.getbbox("W")[3] + 2, p.logo_small // 2 + 2)
        logo_px = row_h - 1
        sections = []
        for title, teams in self._groups(standings, cfg):
            wide = ctx.width >= 96
            sections.append(HBox([Text(title, font, YELLOW), Spacer(), Text("GP  PTS" if wide else "PTS", font, GREY)]))
            for abbrev in teams:
                r = rows.get(abbrev) or {}
                color = WHITE if abbrev in highlight else GREY
                sections.append(HBox([
                    Img(logo(abbrev, logo_px)), Text(abbrev, font, color), Spacer(),
                    Text(f"{r.get('gp', 0):>2}  {r.get('points', 0):>3}" if wide else f"{r.get('points', 0):>3}", font, color),
                ], spacing=2))
            sections.append(Box(0, p.pad))
        body = HBox([Box(p.pad, 0), VBox(sections, spacing=1, align="start"), Box(p.pad, 0)])
        tree = VBox([body, Spacer()], align="start")
        _, total_h = body.measure()
        return render_tree(tree, ctx.width, max(total_h + p.pad, ctx.height))

    def render(self, ctx: BoardContext, cfg: StandingsConfig) -> Image.Image:
        if self._strip is None or self._size != (ctx.width, ctx.height):
            self.enter(ctx, cfg)
        offset = int(max(ctx.elapsed - cfg.hold_seconds, 0) * cfg.scroll_speed)
        offset = min(offset, self._strip.height - ctx.height)
        return self._strip.crop((0, offset, ctx.width, offset + ctx.height))

    def done(self, ctx: BoardContext, cfg: StandingsConfig) -> bool:
        if self._strip is None:
            return False
        travel = self._strip.height - ctx.height
        return ctx.elapsed >= cfg.hold_seconds + travel / cfg.scroll_speed + 1.0

    @staticmethod
    def _favorites(ctx: BoardContext) -> set[str]:
        summary = ctx.snapshot.get("nhl.team_summary") or {}
        return set(summary)
