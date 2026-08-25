"""Favourite team summary: record, streak, last result, next game."""
from __future__ import annotations

from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext
from ...render import HBox, Spacer, Text, VBox, load_font, render_tree
from ...render.layout import Box, Img
from ..teams import logo, team
from .common import DIM, GREY, WHITE, YELLOW, fmt_date, fmt_time, local_time


class TeamSummaryConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Team summary")
    seconds_per_team: float = Field(8.0, ge=3, le=60)
    time_24h: bool = False


class TeamSummaryBoard(BaseBoard):
    key = "nhl.team_summary"
    title = "Team summary"
    config_model = TeamSummaryConfig
    requires = frozenset({"nhl.team_summary"})

    def render(self, ctx: BoardContext, cfg: TeamSummaryConfig) -> Image.Image:
        summaries = list((ctx.snapshot.get("nhl.team_summary") or {}).values())
        if not summaries:
            return Image.new("RGB", (ctx.width, ctx.height))
        s = summaries[int(ctx.elapsed // cfg.seconds_per_team) % len(summaries)]
        t = team(s["abbrev"])
        p = ctx.profile
        rec = s["record"]
        font_s, font_m = load_font("pixel", p.font_small), load_font("pixel", p.font_medium)
        record = f"{rec['wins']}-{rec['losses']}-{rec['otl']}"
        header = VBox([
            Text(s["abbrev"], load_font("block", p.font_medium), WHITE),
            Text(record, font_m, YELLOW),
            Text(f"{rec['points']} PTS", font_s, GREY),
        ], spacing=1)
        rows = [HBox([Img(logo(s["abbrev"], p.logo)), Spacer(), header], spacing=p.pad)]
        if p.width >= 96:
            rows.append(HBox([Text("L10", font_s, DIM), Spacer(), Text(f"{'-'.join(map(str, rec['l10']))}  {rec['streak']}", font_s, GREY)]))
        rows.extend(self._games(s, ctx, cfg))
        while len(rows) > 1 and VBox(rows, spacing=1).measure()[1] > ctx.height - 2 * p.pad:
            rows.pop()
        body = HBox([Box(p.pad, 0), VBox(rows, spacing=1), Box(p.pad, 0)])
        return render_tree(VBox([Spacer(), body, Spacer()]), ctx.width, ctx.height,
                           background=tuple(int(c * 0.25) for c in t.primary))

    def _games(self, s: dict[str, Any], ctx: BoardContext, cfg: TeamSummaryConfig) -> list:
        p = ctx.profile
        font = load_font("pixel", p.font_small)
        rows = []
        prev, nxt = s.get("prev_game"), s.get("next_game")
        if prev:
            vs = "vs" if prev["home"] else "@"
            col = (80, 220, 80) if prev["result"] == "W" else (230, 80, 80)
            rows.append(HBox([Text("LAST", font, DIM), Spacer(min=3),
                              Text(f"{prev['result']} {prev['score']}-{prev['opponent_score']} {vs} {prev['opponent']}", font, col)]))
        if nxt:
            vs = "vs" if nxt["home"] else "@"
            start = local_time(nxt["start_time_utc"], ctx.now.tzinfo)
            when = "TODAY" if start and start.date() == ctx.now.date() else fmt_date(nxt["date"])
            rows.append(HBox([Text("NEXT", font, DIM), Spacer(min=3),
                              Text(f"{when} {fmt_time(start, cfg.time_24h)} {vs} {nxt['opponent']}", font, WHITE)]))
        elif p.height >= 48:
            rows.append(HBox([Text("NEXT", font, DIM), Spacer(min=3), Text("NO GAMES SCHEDULED", font, GREY)]))
        return rows
