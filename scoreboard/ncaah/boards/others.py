"""College hockey ticker / team summary / goal celebration: the NHL boards with the D1 data
keys, school logos and colours, and ranks where they fit. There is no standings board
(ESPN publishes no standings for the league) and no penalty board (no penalty feed)."""
from __future__ import annotations

from typing import Any

from PIL import Image
from pydantic import ConfigDict

from ...boards.base import BoardContext
from ...isotime import parse_iso
from ...nhl.boards.events import GoalBoard as NhlGoalBoard
from ...nhl.boards.events import GoalConfig
from ...nhl.boards.team_summary import TeamSummaryBoard as NhlTeamSummary
from ...nhl.boards.ticker import TickerBoard as NhlTicker
from ..teams import colors, logo, text_on


class NcaahTickerBoard(NhlTicker):
    key = "ncaah.ticker"
    title = "College hockey ticker"
    requires = frozenset({"ncaah.scores"})
    scores_key = "ncaah.scores"
    empty_record = ""               # the feed has no records; printing 0-0-0 for every school would be a lie

    def logo_image(self, abbrev: str, g: dict[str, Any]) -> Image.Image:
        return logo(abbrev, 128)

    def _date_label(self, g: dict[str, Any], ctx: BoardContext) -> str:
        started = parse_iso(g.get("start_time_utc"))
        if started is None:
            return super()._date_label(g, ctx)
        return started.astimezone(ctx.now.tzinfo).strftime("%a").upper()

    def _card(self, g: dict[str, Any], ctx: BoardContext, cfg) -> list:
        if g["phase"] == "pregame":        # the matchup card prints school names; lead ranked ones with the rank
            g = {**g, **{s: {**g[s], "name": f"#{g[s]['rank']} {g[s]['name']}"} for s in ("away", "home") if g[s].get("rank")}}
        return super()._card(g, ctx, cfg)


class NcaahTeamSummaryBoard(NhlTeamSummary):
    key = "ncaah.team_summary"
    title = "College hockey team summary"
    requires = frozenset({"ncaah.team_summary"})
    summary_key = "ncaah.team_summary"

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str):
        primary, _ = colors(abbrev)
        return primary, text_on(primary)

    def _record_lines(self, rec: dict[str, Any]) -> list[str]:
        record = f"{rec['wins']}-{rec['losses']}-{rec.get('ties', rec.get('otl', 0))}"
        if rec.get("rank"):
            record = f"#{rec['rank']} {record}"
        return [f"{record}  GP {rec['gp']}", (rec.get("conference") or "").upper()]


class NcaahGoalConfig(GoalConfig):
    model_config = ConfigDict(frozen=True, extra="forbid", title="College hockey goal celebration")


class NcaahGoalBoard(NhlGoalBoard):
    key = "ncaah.goal"
    title = "College hockey goal celebration"
    config_model = NcaahGoalConfig
    event_kinds = frozenset({"ncaah.goal"})

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str):
        primary, _ = colors(abbrev)
        return primary, text_on(primary)
