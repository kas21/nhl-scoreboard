"""College hockey data source (ESPN, Division I). Publishes ncaah.scores, ncaah.main_event,
ncaah.team_summary and ncaah.season — the NFL loops with the hockey client and normaliser.

ESPN has no standings feed for this league, so there is no ``ncaah.standings``: a favourite's
record is worked out from its own schedule (hourly) and merged into the games it plays, which
is what the game board and ticker print. A Saturday has thirty-odd D1 games, so ``slate``
trims what reaches the ticker and dashboard; the main event is still picked from every game.
"""
from __future__ import annotations

import asyncio
from datetime import tzinfo
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..config.models import ADVANCED
from ..data.source import SourceContext
from ..nfl.source import NflSource, _today, _tz
from .api import NcaahApi, NcaahApiError
from .normalize import (
    normalize_scoreboard,
    record_from_schedule,
    record_text,
    schedule_games,
    team_summary,
)
from .teams import CONFERENCE_OF, NCAAH_TEAMS, REGISTRY_ABBREVS

TeamAbbrev = Literal[NCAAH_TEAMS]  # type: ignore[valid-type]


class NcaahConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="College hockey")
    enabled: bool = True
    favorites: list[TeamAbbrev] = Field([], description="Favourite schools, highest priority first", json_schema_extra={"x-widget": "team-picker"})
    slate: Literal["ranked", "conferences", "all"] = Field(
        "ranked", description="Which games make the ticker and dashboard: games with a top-20 team, "
                              "games in your favourites' conferences, or the whole slate (your favourites' games always count)")
    live_interval: float = Field(15.0, ge=5, le=120, description="Seconds between polls while a favourite is playing", json_schema_extra=ADVANCED)
    idle_interval: float = Field(300.0, ge=60, le=3600, json_schema_extra=ADVANCED)
    standings_interval: float = Field(3600.0, ge=600, description="Seconds between record and schedule refreshes", json_schema_extra=ADVANCED)
    show_games_within_days: int = Field(2, ge=0, le=30, description="Only show the slate when the next game is this close")


def slate(games: list[dict[str, Any]], cfg: NcaahConfig) -> list[dict[str, Any]]:
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


class NcaahSource(NflSource):
    key: ClassVar[str] = "ncaah"
    config_model: ClassVar[type[BaseModel]] = NcaahConfig
    sport: ClassVar[str] = "ncaah"
    label: ClassVar[str] = "College hockey"
    teams: ClassVar[tuple[str, ...]] = NCAAH_TEAMS

    def __init__(self) -> None:
        self._checked = False
        self._records: dict[str, str] = {}      # favourite -> "W-L-T", learned from its schedule

    def _api(self, ctx: SourceContext) -> NcaahApi:
        return NcaahApi(ctx.http)

    def _scoreboard(self, payload: dict[str, Any], tz: tzinfo | None) -> list[dict[str, Any]]:
        return normalize_scoreboard(payload, self._records, tz=tz)

    def _registry_abbrev(self, api_abbrev: str) -> str:
        return REGISTRY_ABBREVS.get(api_abbrev, api_abbrev)

    def _slate(self, games: list[dict[str, Any]], cfg: BaseModel) -> list[dict[str, Any]]:
        return slate(games, cfg)  # type: ignore[arg-type]

    def _season(self, games: list[dict[str, Any]], today: str) -> dict[str, Any]:
        """ESPN's current slate is the season opener until it is played: count down to it."""
        info = super()._season(games, today)
        if games and not any(g["phase"] != "pregame" for g in games) and (info.get("days_to_next") or 0) > 0:
            info = {**info, "phase": "offseason", "regular_start": info["next_game_date"], "days_to_regular": info["days_to_next"],
                    "days_to_preseason": None, "preseason_start": None}
        return info

    def _check_teams(self, ctx: SourceContext, listed: dict[str, str]) -> None:
        """Once: every favourite must be a school ESPN's team API knows (or it has no logo or schedule)."""
        if self._checked or not listed:
            return
        self._checked = True
        cfg: NcaahConfig = ctx.config  # type: ignore[assignment]
        unmapped = sorted(f for f in cfg.favorites if f not in listed)
        if unmapped:
            ctx.log.warning("College hockey: ESPN's team API has no entry for %s (no logo or schedule) — "
                            "it may spell them differently; add the alias to logos.API_ABBREVS['ncaah']", ", ".join(unmapped))

    async def _standings_loop(self, ctx: SourceContext, api: NcaahApi) -> None:      # type: ignore[override]
        """No standings feed exists for this league: this loop keeps the favourites' records and
        schedules (their team summaries) fresh instead, at the standings cadence."""
        while True:
            cfg: NcaahConfig = ctx.config  # type: ignore[assignment]
            if not cfg.enabled:
                await ctx.sleep(60)
                continue
            try:
                ids = self._team_ids((await api.teams())["sports"][0]["leagues"][0]["teams"])
                self._check_teams(ctx, ids)
                today, tz = _today(ctx), _tz(ctx)
                summaries: dict[str, Any] = {}
                records: dict[str, str] = {}
                for abbrev in cfg.favorites:
                    schedule = None
                    if abbrev in ids:
                        try:
                            schedule = await api.team_schedule(ids[abbrev])
                        except NcaahApiError as exc:
                            ctx.log.warning("%s schedule fetch failed for %s: %s", self.label, abbrev, exc)
                    summaries[abbrev] = team_summary(abbrev, schedule, today, tz=tz)
                    if schedule:
                        records[abbrev] = record_text(record_from_schedule(schedule_games(schedule, tz), abbrev))
                self._records = records
                ctx.publish(summaries, subkey="team_summary")
            except (NcaahApiError, KeyError, IndexError) as exc:
                ctx.log.warning("%s record refresh failed: %s", self.label, exc)
            await asyncio.sleep(cfg.standings_interval)
