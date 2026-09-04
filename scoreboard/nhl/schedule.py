"""The league slate for the next few days, from ``/schedule/{date}`` game weeks.

``score/now`` only ever holds one day, so "what's on this week" needs the schedule feed.
The window follows ``show_games_within_days`` — the same setting that decides whether the
panel shows a far-off slate — so the dashboard and the panel agree on how far ahead to look.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .api import NhlApi, NhlApiError
from .normalize import normalize_game

MAX_WEEKS = 6                       # a 30-day window is five weeks; one more is a safe stop


def window_end(today: str, within_days: int) -> str:
    return (date.fromisoformat(today) + timedelta(days=within_days)).isoformat()


def schedule_games(
    weeks: list[dict[str, Any]],
    records: dict[str, str] | None,
    today: str,
    within_days: int,
    follow_preseason: bool = True,
) -> list[dict[str, Any]]:
    """Normalised games dated ``today`` .. ``today + within_days`` from one or more game-week payloads."""
    end = window_end(today, within_days)
    games = []
    for week in weeks:
        for day in week.get("gameWeek") or []:
            day_date = day.get("date", "")
            if not today <= day_date <= end:
                continue
            for raw in day.get("games") or []:
                try:
                    game = normalize_game({**raw, "gameDate": day_date}, records)
                except (KeyError, TypeError, ValueError):
                    continue
                if not follow_preseason and game["type"] == 1:
                    continue
                games.append(game)
    return sorted(games, key=lambda g: (g["date"], g["start_time_utc"]))


async def fetch_weeks(api: NhlApi, first: dict[str, Any], today: str, within_days: int) -> list[dict[str, Any]]:
    """``first`` (the ``/schedule/now`` payload) plus following weeks until the window is covered."""
    end = window_end(today, within_days)
    weeks = [first]
    nxt = first.get("nextStartDate")
    while nxt and nxt <= end and len(weeks) < MAX_WEEKS:
        try:
            week = await api.schedule(nxt)
        except NhlApiError:
            break                                            # what we have still covers the nearer days
        weeks.append(week)
        following = week.get("nextStartDate") or ""
        nxt = following if following > nxt else None                # never loop on a feed that points backwards
    return weeks
