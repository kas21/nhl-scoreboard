"""AHL ticker / standings / team summary / goal / penalty: the NHL boards with the AHL data
keys, logos and colours."""
from __future__ import annotations

from typing import Any

from PIL import Image
from pydantic import ConfigDict

from ...boards.base import BoardContext
from ...nhl.boards.events import GoalBoard as NhlGoalBoard
from ...nhl.boards.events import GoalConfig, PenaltyConfig
from ...nhl.boards.events import PenaltyBoard as NhlPenaltyBoard
from ...nhl.boards.standings import StandingsBoard as NhlStandings
from ...nhl.boards.standings import StandingsConfig
from ...nhl.boards.team_summary import TeamSummaryBoard as NhlTeamSummary
from ...nhl.boards.ticker import TickerBoard as NhlTicker
from ..teams import colors, logo, text_on


def _brand(abbrev: str):
    primary, _ = colors(abbrev)
    return primary, text_on(primary)


class AhlTickerBoard(NhlTicker):
    key = "ahl.ticker"
    title = "AHL ticker"
    requires = frozenset({"ahl.scores"})
    scores_key = "ahl.scores"

    def logo_image(self, abbrev: str, g: dict[str, Any]) -> Image.Image:
        return logo(abbrev, 128)


class AhlStandingsConfig(StandingsConfig):
    model_config = ConfigDict(frozen=True, extra="forbid", title="AHL standings")


class AhlStandingsBoard(NhlStandings):
    key = "ahl.standings"
    title = "AHL standings"
    config_model = AhlStandingsConfig
    requires = frozenset({"ahl.standings"})
    standings_key = "ahl.standings"
    summary_key = "ahl.team_summary"
    season_key = "ahl.season"
    wildcard_cutoff = 0             # the AHL seeds by division; no wildcard line

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str):
        return _brand(abbrev)

    def _grouped(self, standings: dict[str, Any], cfg: StandingsConfig) -> list[list[tuple[str, list[str], bool]]]:
        if cfg.view == "league":
            return [[("AHL", standings.get("league", []), False)]]
        if cfg.view == "wildcard":          # conference pages: the two divisions each, no cutoff line
            pages = [[(f"{conf} {div}".upper(), teams, False) for div, teams in divs.items()]
                     for conf, divs in (standings.get("wildcard") or {}).items()]
            return pages or [[]]
        return super()._grouped(standings, cfg)


class AhlTeamSummaryBoard(NhlTeamSummary):
    key = "ahl.team_summary"
    title = "AHL team summary"
    requires = frozenset({"ahl.team_summary"})
    summary_key = "ahl.team_summary"

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str):
        return _brand(abbrev)


class AhlGoalConfig(GoalConfig):
    model_config = ConfigDict(frozen=True, extra="forbid", title="AHL goal celebration")


class AhlGoalBoard(NhlGoalBoard):
    key = "ahl.goal"
    title = "AHL goal celebration"
    config_model = AhlGoalConfig
    event_kinds = frozenset({"ahl.goal"})

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str):
        return _brand(abbrev)


class AhlPenaltyConfig(PenaltyConfig):
    model_config = ConfigDict(frozen=True, extra="forbid", title="AHL penalty alert")


class AhlPenaltyBoard(NhlPenaltyBoard):
    key = "ahl.penalty"
    title = "AHL penalty alert"
    config_model = AhlPenaltyConfig
    event_kinds = frozenset({"ahl.penalty"})

    def team_colors(self, abbrev: str):
        return _brand(abbrev)


__all__ = ["AhlGoalBoard", "AhlGoalConfig", "AhlPenaltyBoard", "AhlPenaltyConfig", "AhlStandingsBoard", "AhlStandingsConfig",
           "AhlTeamSummaryBoard", "AhlTickerBoard", "BoardContext"]
