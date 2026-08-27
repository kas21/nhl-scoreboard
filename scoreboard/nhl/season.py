"""Season phase from the NHL schedule metadata (pure)."""
from __future__ import annotations

from datetime import date
from typing import Any

PHASES = ("offseason", "preseason", "regular", "playoffs")


def season_info(schedule_now: dict[str, Any], today: date, standings_season_id: int | None = None,
                club_schedule: dict[str, Any] | None = None, abbrev: str | None = None) -> dict[str, Any]:
    """{phase, dates..., days_to_preseason/regular, standings_final, first_game}."""
    def d(key: str) -> date | None:
        v = schedule_now.get(key)
        try:
            return date.fromisoformat(v) if v else None
        except ValueError:
            return None
    pre, reg, reg_end, po_end = d("preSeasonStartDate"), d("regularSeasonStartDate"), d("regularSeasonEndDate"), d("playoffEndDate")
    if reg and today >= reg and reg_end and today <= reg_end:
        phase = "regular"
    elif reg_end and today > reg_end and po_end and today <= po_end:
        phase = "playoffs"
    elif pre and reg and pre <= today < reg:
        phase = "preseason"
    else:
        phase = "offseason"
    season_id = None
    games = (club_schedule or {}).get("games") or []
    if club_schedule and club_schedule.get("currentSeason"):
        season_id = int(club_schedule["currentSeason"])
    elif reg:
        season_id = reg.year * 10000 + reg.year + 1
    first_game = None
    if abbrev:
        for g in games:
            if g.get("gameType") == 2 and g.get("gameDate"):
                us_home = (g.get("homeTeam") or {}).get("abbrev") == abbrev
                them = (g.get("awayTeam") if us_home else g.get("homeTeam")) or {}
                first_game = {"date": g["gameDate"], "home": us_home, "opponent": them.get("abbrev", ""), "start_time_utc": g.get("startTimeUTC", "")}
                break
    return {
        "sport": "nhl", "phase": phase, "season_id": season_id,
        "preseason_start": pre.isoformat() if pre else None, "regular_start": reg.isoformat() if reg else None,
        "regular_end": reg_end.isoformat() if reg_end else None, "playoff_end": po_end.isoformat() if po_end else None,
        "days_to_preseason": (pre - today).days if pre else None, "days_to_regular": (reg - today).days if reg else None,
        "standings_season_id": standings_season_id,
        "standings_final": bool(standings_season_id and season_id and standings_season_id < season_id),
        "first_game": first_game,
    }
