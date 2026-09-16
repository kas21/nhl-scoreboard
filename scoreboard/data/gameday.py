"""The game day as the scoreboard sees it.

A game belongs to its local calendar date, and the sources pick tonight's slate and the main event
from *today's* games. ``sports.game_day_rollover_hour`` adds a grace window after midnight: until
that hour, last night's finished games stay in the scores list (the ticker) alongside today's.
It never delays today's games and never keeps a finished game as the main event — the
postgame board leaves at midnight as before; only the ticker keeps the results.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

NOT_PLAYED = frozenset({"PPD", "CANCELLED", "SUSPENDED"})     # outcome labels a rained-out or postponed game carries
IN_PROGRESS = frozenset({"live", "intermission"})


class _DayContext(Protocol):
    timezone: str | None
    game_day_rollover_hour: int


def local_now(ctx: _DayContext) -> datetime:
    """Now in the configured timezone (falls back to the machine's local clock)."""
    tz = getattr(ctx, "timezone", None)
    try:
        return datetime.now(ZoneInfo(tz)) if tz else datetime.now().astimezone()
    except Exception:
        return datetime.now().astimezone()


def local_today(ctx: _DayContext) -> str:
    return local_now(ctx).date().isoformat()


def yesterday(today: str) -> str:
    return (date.fromisoformat(today) - timedelta(days=1)).isoformat()


def carry_last_night(ctx: _DayContext) -> bool:
    """True while last night's finals still belong on the ticker: before the rollover hour."""
    return local_now(ctx).hour < int(getattr(ctx, "game_day_rollover_hour", 0) or 0)


def is_last_nights(game: dict[str, Any], today: str) -> bool:
    """A game dated yesterday that was played: finished, or still going past midnight.

    Postponed / cancelled / suspended games and anything not yet started are not results and drop
    with the old date.
    """
    if game.get("date") != yesterday(today):
        return False
    if game.get("phase") in IN_PROGRESS:
        return True
    return game.get("phase") == "postgame" and game.get("outcome") not in NOT_PLAYED
