"""College football ticker / standings / team summary / scoring alert: the NFL boards with
FBS data keys, logos and colours, ranks where they fit, and conference standings."""
from __future__ import annotations

from typing import Any

from PIL import Image
from pydantic import ConfigDict, Field

from ...boards.base import BoardContext
from ...nfl.boards.others import (
    NflScoreBoard,
    NflStandingsBoard,
    NflTeamSummaryBoard,
    NflTickerBoard,
    ScoreConfig,
)
from ...nhl.boards.standings import StandingsConfig
from ..teams import colors, logo, text_on

ORDINALS = {1: "1ST", 2: "2ND", 3: "3RD"}


def ordinal(n: int | None) -> str:
    return ORDINALS.get(n, f"{n}TH") if n else ""


class NcaafTickerBoard(NflTickerBoard):
    key = "ncaaf.ticker"
    title = "College football ticker"
    requires = frozenset({"ncaaf.scores"})
    scores_key = "ncaaf.scores"

    def logo_image(self, abbrev: str, g: dict[str, Any]) -> Image.Image:
        return logo(abbrev, 128)

    def _card(self, g: dict[str, Any], ctx: BoardContext, cfg) -> list:
        if g["phase"] == "pregame":        # the matchup card prints school names; lead ranked ones with the rank
            g = {**g, **{s: {**g[s], "name": f"#{g[s]['rank']} {g[s]['name']}"} for s in ("away", "home") if g[s].get("rank")}}
        return super()._card(g, ctx, cfg)


class NcaafStandingsConfig(StandingsConfig):
    model_config = ConfigDict(frozen=True, extra="forbid", title="College football standings")
    favorite_conferences_only: bool = Field(True, description="Show only your favourites' conferences (all ten when you have no favourite)")


class NcaafStandingsBoard(NflStandingsBoard):
    key = "ncaaf.standings"
    title = "College football standings"
    config_model = NcaafStandingsConfig
    requires = frozenset({"ncaaf.standings"})
    standings_key = "ncaaf.standings"
    summary_key = "ncaaf.team_summary"
    season_key = "ncaaf.season"
    points_header = "CONF"

    def __init__(self) -> None:
        super().__init__()
        self._favorite_confs: set[str] = set()

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str):
        primary, _ = colors(abbrev)
        return primary, text_on(primary)

    def enter(self, ctx: BoardContext, cfg: StandingsConfig) -> None:
        rows = (ctx.snapshot.get(self.standings_key) or {}).get("teams") or {}
        favs = {h.upper() for h in cfg.highlight} or set(ctx.snapshot.get(self.summary_key) or {})
        self._favorite_confs = {rows[f]["conference"] for f in favs if f in rows}
        super().enter(ctx, cfg)

    def _grouped(self, standings: dict[str, Any], cfg: StandingsConfig) -> list[list[tuple[str, list[str], bool]]]:
        only = getattr(cfg, "favorite_conferences_only", True) and self._favorite_confs
        if cfg.view == "league":
            return [[("FBS", standings.get("league", []), False)]]
        if cfg.view == "wildcard":
            pages = [[(f"{conf} {div}".upper(), teams, False) for div, teams in divs.items()]
                     for conf, divs in (standings.get("wildcard") or {}).items() if not only or conf in self._favorite_confs]
            return pages or [[]]
        pages = [[(conf.upper(), teams, False)] for conf, teams in (standings.get("division") or {}).items()
                 if not only or conf in self._favorite_confs]
        return pages or [[]]

    def _points(self, r: dict[str, Any]) -> str:
        return r.get("conf_record") or "0-0"


class NcaafTeamSummaryBoard(NflTeamSummaryBoard):
    key = "ncaaf.team_summary"
    title = "College football team summary"
    requires = frozenset({"ncaaf.team_summary"})
    summary_key = "ncaaf.team_summary"

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str):
        primary, _ = colors(abbrev)
        return primary, text_on(primary)

    def _record_lines(self, rec: dict[str, Any]) -> list[str]:
        line1 = f"{rec.get('wins', 0)}-{rec.get('losses', 0)}"
        if rec.get("rank"):
            line1 = f"#{rec['rank']} {line1}"
        conf = (rec.get("conference") or rec.get("division") or "").upper()
        line2 = f"{conf} {rec.get('conf_record', '')} {ordinal(rec.get('conference_rank'))}".strip()
        return [line1, " ".join(line2.split())]


class NcaafScoreConfig(ScoreConfig):
    model_config = ConfigDict(frozen=True, extra="forbid", title="College football scoring alert")


class NcaafScoreBoard(NflScoreBoard):
    key = "ncaaf.score"
    title = "College football scoring alert"
    config_model = NcaafScoreConfig
    event_kinds = frozenset({"ncaaf.touchdown", "ncaaf.field_goal", "ncaaf.safety", "ncaaf.score"})

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str):
        return colors(abbrev)
