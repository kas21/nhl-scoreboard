"""College football data source (ESPN, FBS). Publishes ncaaf.scores, ncaaf.main_event,
ncaaf.standings, ncaaf.team_summary and ncaaf.season — the NFL loops with the FBS client.

A Saturday has sixty-odd FBS games, so ``slate`` trims what reaches the ticker and the
dashboard; the main event is still picked from every game, so a favourite is never lost.
"""
from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..config.models import ADVANCED
from ..data.source import SourceContext
from ..nfl.source import NflSource
from .api import NcaafApi
from .normalize import normalize_scoreboard, normalize_standings, team_summary
from .teams import CONFERENCE_OF, NCAAF_TEAMS

TeamAbbrev = Literal[NCAAF_TEAMS]  # type: ignore[valid-type]


class NcaafConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="College football")
    enabled: bool = True
    favorites: list[TeamAbbrev] = Field([], description="Favourite schools, highest priority first", json_schema_extra={"x-widget": "team-picker"})
    slate: Literal["ranked", "conferences", "all"] = Field(
        "ranked", description="Which FBS games make the ticker and dashboard: games with a top-25 team, "
                              "games in your favourites' conferences, or the whole slate (your favourites' games always count)")
    live_interval: float = Field(20.0, ge=10, le=120, description="Seconds between polls while a favourite is playing", json_schema_extra=ADVANCED)
    idle_interval: float = Field(300.0, ge=60, le=3600, json_schema_extra=ADVANCED)
    standings_interval: float = Field(3600.0, ge=600, json_schema_extra=ADVANCED)
    show_games_within_days: int = Field(2, ge=0, le=30, description="Only show the week's slate when the next game is this close")


def slate(games: list[dict[str, Any]], cfg: NcaafConfig) -> list[dict[str, Any]]:
    favs = {f.upper() for f in cfg.favorites}
    confs = {CONFERENCE_OF.get(f) for f in favs} - {None}

    def keep(g: dict[str, Any]) -> bool:
        sides = (g["away"], g["home"])
        if any(s["abbrev"] in favs for s in sides):
            return True
        if cfg.slate == "ranked":
            return any(s.get("rank") for s in sides)
        if cfg.slate == "conferences":
            return any(CONFERENCE_OF.get(s["abbrev"]) in confs for s in sides)
        return True

    return [g for g in games if keep(g)]


class NcaafSource(NflSource):
    key: ClassVar[str] = "ncaaf"
    config_model: ClassVar[type[BaseModel]] = NcaafConfig
    sport: ClassVar[str] = "ncaaf"
    label: ClassVar[str] = "College football"
    teams: ClassVar[tuple[str, ...]] = NCAAF_TEAMS

    def __init__(self) -> None:
        self._checked = False

    def _api(self, ctx: SourceContext) -> NcaafApi:
        return NcaafApi(ctx.http)

    def _scoreboard(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return normalize_scoreboard(payload)

    def _standings(self, payload: dict[str, Any]) -> dict[str, Any]:
        return normalize_standings(payload)

    def _summary(self, abbrev: str, standings: dict[str, Any], schedule: dict[str, Any] | None, today: str) -> dict[str, Any]:
        return team_summary(abbrev, standings, schedule, today)

    def _slate(self, games: list[dict[str, Any]], cfg: BaseModel) -> list[dict[str, Any]]:
        return slate(games, cfg)  # type: ignore[arg-type]

    def _check_teams(self, ctx: SourceContext, listed: dict[str, str]) -> None:
        """Realignment guard: say once which registry entries ESPN no longer knows, and vice versa."""
        if self._checked or not listed:
            return
        self._checked = True
        stale = sorted(set(NCAAF_TEAMS) - set(listed))
        unknown = sorted(set(listed) - set(NCAAF_TEAMS))
        if stale:
            ctx.log.warning("College football: ESPN lists no FBS team for %s — update scoreboard/ncaaf/teams.py", ", ".join(stale))
        if unknown:
            ctx.log.info("College football: FBS teams not in the registry (cannot be favourites yet): %s", ", ".join(unknown))
