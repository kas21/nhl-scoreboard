"""Demo source: replays the recorded fixture game as if it were live.

Lets you exercise every state, the rotation and goal/penalty alerts with no
NHL games on (and no network). Enabled with ``scoreboard --demo``.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from .data.source import SourceContext
from .nhl.normalize import normalize_game, normalize_standings, records_from_standings, team_summary
from .nhl.select import favorite_side

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures" / "nhl"


class DemoConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", title="Demo")
    favorites: list[str] = ["TOR"]
    seconds_per_step: float = Field(6.0, ge=1, le=60)


class DemoSource:
    key: ClassVar[str] = "nhl"          # same key so boards see nhl.* data
    config_model: ClassVar[type[BaseModel]] = DemoConfig

    async def run(self, ctx: SourceContext) -> None:
        score = json.loads((FIXTURES / "score_2026-04-11.json").read_text())
        standings = normalize_standings(json.loads((FIXTURES / "standings_2026-04-10.json").read_text()))
        landing = json.loads((FIXTURES / "landing_2025021270.json").read_text())
        recs = records_from_standings(standings)
        games = [normalize_game(g, recs) for g in score["games"]]
        ctx.publish(standings, subkey="standings")
        ctx.publish(games, subkey="scores")
        ctx.publish({"TOR": team_summary("TOR", standings, json.loads((FIXTURES / "club_schedule_TOR_week.json").read_text()), "2026-04-11")}, subkey="team_summary")
        ctx.publish_to("system", {"online": True})
        raw = next(g for g in score["games"] if g["homeTeam"]["abbrev"] == "TOR")
        final = normalize_game(raw, recs, landing)
        while True:
            for step in _script(final):
                cfg: DemoConfig = ctx.config  # type: ignore[assignment]
                if step is not None:
                    step = {**step, "favorite_side": favorite_side(step, cfg.favorites)}
                if step is not None:
                    step = {**step, "sport": "nhl"}
                ctx.publish_to("nhl.main_event", step)
                await asyncio.sleep(cfg.seconds_per_step)


def _script(final: dict[str, Any]) -> list[dict[str, Any]]:
    """A short game: pregame → live (goal, penalty, PP, EN) → intermission → final → offday."""
    zero = {**final, "outcome": "", "goals": [], "penalties": [],
            "away": {**final["away"], "score": 0, "sog": 0}, "home": {**final["home"], "score": 0, "sog": 0}}
    away_goals = [g for g in final["goals"] if g["team"] == final["away"]["abbrev"]]
    home_goals = [g for g in final["goals"] if g["team"] == final["home"]["abbrev"]]
    goals = [away_goals[0], home_goals[0]] if away_goals and home_goals else final["goals"][:2]
    pens = final["penalties"]
    live = {**zero, "state": "LIVE", "phase": "live", "period": "1st", "period_number": 1, "clock": "18:20", "clock_running": True,
            "away": {**zero["away"], "sog": 3}, "home": {**zero["home"], "sog": 4}}
    return [
        {**zero, "state": "FUT", "phase": "pregame", "start_time_utc": "2026-04-11T23:00:00Z"},
        live,
        {**live, "clock": "15:02", "goals": goals[:1], "away": {**live["away"], "score": 1, "sog": 6}},
        {**live, "clock": "12:40", "goals": goals[:1], "penalties": pens[:1], "powerplay": {"code": "a54", "clock": "02:00"},
         "away": {**live["away"], "score": 1, "sog": 8}},
        {**live, "clock": "00:00", "clock_running": False, "in_intermission": True, "phase": "intermission",
         "goals": goals[:1], "penalties": pens[:1], "away": {**live["away"], "score": 1, "sog": 11}},
        {**live, "period": "3rd", "period_number": 3, "clock": "08:11", "goals": goals[:2], "penalties": pens[:1],
         "away": {**live["away"], "score": 1, "sog": 20}, "home": {**live["home"], "score": 1, "sog": 15}},
        {**live, "period": "3rd", "period_number": 3, "clock": "01:30", "state": "CRIT", "goals": goals[:2], "penalties": pens[:1],
         "pulled_goalie": 2, "powerplay": {"code": "h65", "clock": ""},
         "away": {**live["away"], "score": 1, "sog": 24}, "home": {**live["home"], "score": 1, "sog": 19}},
        {**final, "favorite_side": None},
        None,
    ]
