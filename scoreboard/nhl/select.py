"""Pick the main event from today's games given the favourite-team priority list."""
from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from ..isotime import parse_iso

# Every state the sport normalisers emit. NHL: LIVE/CRIT/PRE/FUT/OVER/FINAL/OFF; ESPN football:
# PRE/LIVE/HALF/POST; anything unknown sorts last, so a new state is never mistaken for a live one.
_PRIORITY = {"LIVE": 0, "CRIT": 0, "HALF": 0, "PRE": 1, "FUT": 2, "OVER": 3, "FINAL": 3, "OFF": 3, "POST": 3}
ACTIVE_STATES = frozenset({"LIVE", "CRIT", "HALF"})


def select_main_event(games: list[dict[str, Any]], favorites: list[str], today: str | None = None,
                      timezone: str | None = None) -> dict[str, Any] | None:
    """Live favourite game first (by favourite order), else the highest-priority favourite game.

    Only games on ``today`` (YYYY-MM-DD, the viewer's local date) or currently active count:
    the score feed returns the *next* game day when there are no games today, and a game a
    week out must not put the board into "pregame". A game is on today when its league date
    says so *or* when its start falls on today in the viewer's ``timezone``: the NHL dates
    games by the Eastern day, so a 7 pm ET puck drop is already tomorrow for anyone east of
    the Atlantic, and without the second test they never saw a pregame or postgame board.
    """
    best: tuple[int, int, dict[str, Any]] | None = None
    for rank, team in enumerate(t.upper() for t in favorites):
        for g in games:
            if team not in (g["away"]["abbrev"], g["home"]["abbrev"]):
                continue
            if today and not on_day(g, today, timezone) and g["state"] not in ACTIVE_STATES:
                continue
            key = (_PRIORITY.get(g["state"], 9), rank)
            if best is None or key < best[:2]:
                best = (*key, g)
    return best[2] if best else None


def on_day(game: dict[str, Any], today: str, timezone: str | None) -> bool:
    if game.get("date") == today:
        return True
    if not timezone:
        return False
    started = parse_iso(str(game.get("start_time_utc") or ""))
    if started is None:
        return False
    try:
        return started.astimezone(ZoneInfo(timezone)).date().isoformat() == today
    except Exception:
        return False


def favorite_side(game: dict[str, Any], favorites: list[str]) -> str | None:
    favs = [f.upper() for f in favorites]
    for side in ("away", "home"):
        if game[side]["abbrev"] in favs:
            return side
    return None
