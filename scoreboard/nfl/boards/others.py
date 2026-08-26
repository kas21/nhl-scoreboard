"""NFL ticker / standings / team summary: the NHL boards with NFL data keys, logos and colours."""
from __future__ import annotations

from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BoardContext, EventBoard, SequenceMixin
from ...data import Event
from ...nhl.boards.events import celebration_frames
from ...nhl.boards.standings import StandingsBoard as NhlStandings
from ...nhl.boards.team_summary import TeamSummaryBoard as NhlTeamSummary
from ...nhl.boards.ticker import TickerBoard as NhlTicker
from ...render import Sequence, Spacer, Text, VBox, fit_font, load_font, render_tree
from ..teams import colors, logo, text_on
from .game import WHITE


class NflTickerBoard(NhlTicker):
    key = "nfl.ticker"
    title = "NFL ticker"
    requires = frozenset({"nfl.scores"})
    scores_key = "nfl.scores"

    def logo_image(self, abbrev: str, g: dict[str, Any]) -> Image.Image:
        return logo(abbrev, 128)

    def _date_label(self, g: dict[str, Any], ctx: BoardContext) -> str:
        try:
            from datetime import datetime
            return datetime.fromisoformat(g["start_time_utc"].replace("Z", "+00:00")).astimezone(ctx.now.tzinfo).strftime("%a").upper()
        except ValueError:
            return super()._date_label(g, ctx)


class NflStandingsBoard(NhlStandings):
    key = "nfl.standings"
    title = "NFL standings"
    requires = frozenset({"nfl.standings"})
    standings_key = "nfl.standings"
    summary_key = "nfl.team_summary"
    points_header = "PCT"

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str):
        primary, _ = colors(abbrev)
        return primary, text_on(primary)

    def _record(self, r: dict[str, Any]) -> str:
        return f"{r.get('wins', 0)}-{r.get('losses', 0)}" + (f"-{r.get('otl', 0)}" if r.get("otl") else "")

    def _points(self, r: dict[str, Any]) -> str:
        return (r.get("win_pct") or "").lstrip("0") or ".000"


class NflTeamSummaryBoard(NhlTeamSummary):
    key = "nfl.team_summary"
    title = "NFL team summary"
    requires = frozenset({"nfl.team_summary"})
    summary_key = "nfl.team_summary"

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str):
        primary, _ = colors(abbrev)
        return primary, text_on(primary)

    def _record_lines(self, rec: dict[str, Any]) -> list[str]:
        rec_txt = f"{rec['wins']}-{rec['losses']}" + (f"-{rec['otl']}" if rec.get("otl") else "")
        rank = rec.get("division_rank")
        ordinal = {1: "1ST", 2: "2ND", 3: "3RD", 4: "4TH"}.get(rank, "")
        line2 = f"{rec.get('division', '')} {ordinal}".strip()
        if rec.get("bye_week"):
            line2 = f"{line2}  BYE WK{rec['bye_week']}"
        return [f"{rec_txt}  {rec.get('win_pct', '')}".strip(), line2.strip()]


class ScoreConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="NFL scoring alert")
    enabled: bool = True
    touchdown_seconds: float = Field(8.0, ge=2, le=30)
    other_seconds: float = Field(4.0, ge=1, le=15, description="Field goals and safeties")
    opponent_scores: bool = Field(True, description="Also alert (briefly) when the other team scores")


class NflScoreBoard(SequenceMixin, EventBoard):
    key = "nfl.score"
    title = "NFL scoring alert"
    config_model = ScoreConfig
    event_kinds = frozenset({"nfl.touchdown", "nfl.field_goal", "nfl.safety", "nfl.score"})

    def matches(self, event: Event, cfg: ScoreConfig) -> bool:
        if not cfg.enabled or event.kind not in self.event_kinds:
            return False
        game = event.payload.get("game") or {}
        return game.get("favorite_side") == event.payload.get("side") or cfg.opponent_scores

    def build(self, ctx: BoardContext, cfg: ScoreConfig) -> Sequence:
        ev = ctx.event
        payload = ev.payload if ev else {}
        game = payload.get("game") or {}
        side = payload.get("side", "away")
        abbrev = game.get(side, {}).get("abbrev", ev.team if ev else "")
        primary = tuple(game.get(side, {}).get("color") or colors(abbrev)[0])
        seq = Sequence(ctx.fps)
        is_fav = game.get("favorite_side") == side
        kind = ev.kind if ev else "nfl.score"
        if kind == "nfl.touchdown" and is_fav:
            seq.frames(celebration_frames("TOUCHDOWN!", logo(abbrev, 128), primary, ctx.width, ctx.height, cfg.touchdown_seconds, ctx.fps))
        else:
            word = {"nfl.touchdown": "TOUCHDOWN", "nfl.field_goal": "FIELD GOAL", "nfl.safety": "SAFETY"}.get(kind, "SCORE")
            p = ctx.profile
            rows = [Spacer(), Text(abbrev, load_font("block", p.font_medium), text_on(primary)),
                    Text(word, fit_font(word, "block", ctx.width - 2 * p.pad, p.font_large), WHITE),
                    Text(payload.get("score", ""), load_font("pl", 6), text_on(primary)), Spacer()]
            still = render_tree(VBox(rows, spacing=1), ctx.width, ctx.height, background=primary)
            seq.flash(primary, times=2, secs=0.4).slide_in("up", 0.3).hold(cfg.touchdown_seconds if kind == "nfl.touchdown" else cfg.other_seconds).fade_out(0.4)
            return seq.build(still)
        return seq.build(Image.new("RGB", (ctx.width, ctx.height)))
