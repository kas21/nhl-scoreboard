"""Pick the main event from today's games given the favourite-team priority list."""
from __future__ import annotations

from typing import Any

_PRIORITY = {"LIVE": 0, "CRIT": 0, "PRE": 1, "FUT": 2, "OVER": 3, "FINAL": 3, "OFF": 3}


def select_main_event(games: list[dict[str, Any]], favorites: list[str]) -> dict[str, Any] | None:
    """Live favourite game first (by favourite order), else the highest-priority favourite game."""
    best: tuple[int, int, dict[str, Any]] | None = None
    for rank, team in enumerate(t.upper() for t in favorites):
        for g in games:
            if team not in (g["away"]["abbrev"], g["home"]["abbrev"]):
                continue
            key = (_PRIORITY.get(g["state"], 9), rank)
            if best is None or key < best[:2]:
                best = (*key, g)
    return best[2] if best else None


def favorite_side(game: dict[str, Any], favorites: list[str]) -> str | None:
    favs = [f.upper() for f in favorites]
    for side in ("away", "home"):
        if game[side]["abbrev"] in favs:
            return side
    return None
