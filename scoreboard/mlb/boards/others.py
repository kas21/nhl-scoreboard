"""MLB ticker / standings / team summary (the NHL boards with MLB data keys, logos and
colours) and the run / home-run alert."""
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
from ..normalize import last_name
from ..teams import logo, team
from .game import WHITE

ORDINALS = {1: "1ST", 2: "2ND", 3: "3RD", 4: "4TH", 5: "5TH"}


class MlbTickerBoard(NhlTicker):
    key = "mlb.ticker"
    title = "MLB ticker"
    requires = frozenset({"mlb.scores"})
    scores_key = "mlb.scores"

    def logo_image(self, abbrev: str, g: dict[str, Any]) -> Image.Image:
        return logo(abbrev, 128)


class MlbStandingsBoard(NhlStandings):
    key = "mlb.standings"
    title = "MLB standings"
    requires = frozenset({"mlb.standings"})
    standings_key = "mlb.standings"
    summary_key = "mlb.team_summary"
    season_key = "mlb.season"
    points_header = "GB"
    wildcard_cutoff = 3

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str):
        t = team(abbrev)
        return t.primary, t.text_on_primary

    _view = "division"

    def enter(self, ctx: BoardContext, cfg) -> None:
        self._view = getattr(cfg, "view", "division")          # GB is relative to the view: division or wild card race
        super().enter(ctx, cfg)

    def _record(self, r: dict[str, Any]) -> str:
        return f"{r.get('wins', 0)}-{r.get('losses', 0)}"

    def _points(self, r: dict[str, Any]) -> str:
        if self._view == "wildcard" and r.get("division_rank") != 1:
            return str(r.get("wildcard_games_back") or "-")
        return str(r.get("games_back") or "-")

    def _banner(self, ctx: BoardContext) -> str | None:
        season = ctx.snapshot.get(self.season_key) or {}
        sid = season.get("standings_season_id")
        return f"FINAL {sid}" if season.get("standings_final") and sid else None


class MlbTeamSummaryBoard(NhlTeamSummary):
    key = "mlb.team_summary"
    title = "MLB team summary"
    requires = frozenset({"mlb.team_summary"})
    summary_key = "mlb.team_summary"

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str):
        t = team(abbrev)
        return t.primary, t.text_on_primary

    def _record_lines(self, rec: dict[str, Any]) -> list[str]:
        pct = (rec.get("win_pct") or "").lstrip("0")
        line1 = f"{rec.get('wins', 0)}-{rec.get('losses', 0)}  {pct}".strip()
        rank = ORDINALS.get(rec.get("division_rank"), "")
        line2 = f"{rec.get('division', '')} {rank}".strip().upper()
        gb = rec.get("games_back") or "-"
        if gb not in ("-", "0.0", "0"):
            line2 = f"{line2}  {gb} GB"
        l10 = rec.get("l10") or []
        line3 = f"L10 {l10[0]}-{l10[1]}" if len(l10) == 2 else ""
        return [line for line in (line1, line2, line3) if line]


class ScoreConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="MLB scoring alert")
    enabled: bool = True
    home_run_seconds: float = Field(8.0, ge=2, le=30, description="Length of the HOME RUN! celebration")
    run_seconds: float = Field(4.0, ge=1, le=15, description="Length of the plain run alert")
    runs: bool = Field(True, description="Also alert on runs that are not home runs")
    opponent_scores: bool = Field(False, description="Also alert (briefly) when the other team scores")


class MlbScoreBoard(SequenceMixin, EventBoard):
    key = "mlb.score"
    title = "MLB scoring alert"
    config_model = ScoreConfig
    event_kinds = frozenset({"mlb.home_run", "mlb.run"})

    def matches(self, event: Event, cfg: ScoreConfig) -> bool:
        if not cfg.enabled or event.kind not in self.event_kinds:
            return False
        if event.kind == "mlb.run" and not cfg.runs:
            return False
        game = event.payload.get("game") or {}
        return game.get("favorite_side") == event.payload.get("side") or cfg.opponent_scores

    def build(self, ctx: BoardContext, cfg: ScoreConfig) -> Sequence:
        ev = ctx.event
        payload = ev.payload if ev else {}
        game = payload.get("game") or {}
        side = payload.get("side", "away")
        abbrev = game.get(side, {}).get("abbrev", ev.team if ev else "")
        t = team(abbrev)
        seq = Sequence(ctx.fps)
        is_fav = game.get("favorite_side") == side
        kind = ev.kind if ev else "mlb.run"
        if kind == "mlb.home_run" and is_fav:
            seq.frames(celebration_frames("HOME RUN!", logo(abbrev, 128), t.primary, ctx.width, ctx.height, cfg.home_run_seconds, ctx.fps))
            return seq.build(Image.new("RGB", (ctx.width, ctx.height)))
        runs = int(payload.get("runs") or 1)
        word = "HOME RUN" if kind == "mlb.home_run" else "RUN" if runs == 1 else f"{runs} RUNS"
        who = last_name(payload.get("batter") or "").upper()
        p = ctx.profile
        rows = [Spacer(), Text(abbrev, load_font("block", p.font_medium), t.text_on_primary),
                Text(word, fit_font(word, "block", ctx.width - 2 * p.pad, p.font_large), WHITE),
                Text(f"{who}  {payload.get('score', '')}".strip(), ctx.profile.label_font(), t.text_on_primary), Spacer()]
        still = render_tree(VBox(rows, spacing=1), ctx.width, ctx.height, background=t.primary)
        secs = cfg.home_run_seconds if kind == "mlb.home_run" else cfg.run_seconds
        seq.flash(t.primary, times=2, secs=0.4).slide_in("up", 0.3).hold(secs).fade_out(0.4)
        return seq.build(still)
