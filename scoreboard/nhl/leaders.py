"""Scoring and goaltending leaders, for the league and for one club.

Two feeds, one shape. The league's comes from ``skater-stats-leaders`` and
``goalie-stats-leaders`` (a ranked list per category, each row carrying only that category's
number); a club's from ``club-stats/{team}/now`` (every player's whole line, ranked here).
Both normalise to::

    {season, game_type, goals: [skater], assists: [skater], points: [skater],
     goalies: {wins: [goalie], save_pct: [goalie], gaa: [goalie], shutouts: [goalie]}}

with at most ``KEEP`` rows per list, best first. A skater row is ``{id, first, last, team,
position, value}``; a goalie row ``{id, first, last, team, wins, save_pct, gaa, shutouts}``,
the same four numbers whichever list it is in, so a board can rank by one and show the rest.
``save_pct`` and ``gaa`` are None for a goalie the league does not rank (too few games).
"""
from __future__ import annotations

from typing import Any

from .normalize import _text

KEEP = 10
SKATER_CATEGORIES: tuple[str, ...] = ("goals", "assists", "points")
# our name -> (the leaders feed's category, the club-stats field, lower is better)
GOALIE_STATS: dict[str, tuple[str, str, bool]] = {
    "wins": ("wins", "wins", False),
    "save_pct": ("savePctg", "savePercentage", False),
    "gaa": ("goalsAgainstAverage", "goalsAgainstAverage", True),
    "shutouts": ("shutouts", "shutouts", False),
}
GOALIE_FEED_CATEGORIES: tuple[str, ...] = tuple(feed for feed, _, _ in GOALIE_STATS.values())
COUNTS = ("wins", "shutouts")      # a goalie missing from these lists has none; from the other two, is unranked


def normalize_league_leaders(skaters: dict[str, Any] | None, goalies: dict[str, Any] | None) -> dict[str, Any]:
    """The ``nhl.leaders`` value from the two leaders payloads (either may be None: a fetch that
    failed leaves its lists empty rather than taking the other half down)."""
    skaters, goalies = skaters or {}, goalies or {}
    value: dict[str, Any] = {
        "season": _season(skaters.get("season") or goalies.get("season")),
        "game_type": _int(skaters.get("gameType") or goalies.get("gameType")),
    }
    for category in SKATER_CATEGORIES:
        value[category] = [_league_skater(row) for row in _rows(skaters.get(category))[:KEEP]]
    # A goalie's line is spread over the four lists: gather it by player, then rank by each.
    lines: dict[Any, dict[str, Any]] = {}
    for stat, (feed, _, _) in GOALIE_STATS.items():
        for row in _rows(goalies.get(feed)):
            line = lines.setdefault(row.get("id"), {**_who(row, row.get("id"), row.get("teamAbbrev")),
                                                    "wins": 0, "save_pct": None, "gaa": None, "shutouts": 0})
            line[stat] = _int(row.get("value")) or 0 if stat in COUNTS else _float(row.get("value"))
    value["goalies"] = {stat: [lines[row.get("id")] for row in _rows(goalies.get(feed))[:KEEP]]
                        for stat, (feed, _, _) in GOALIE_STATS.items()}
    return value


def normalize_team_leaders(club: dict[str, Any], team: str) -> dict[str, Any]:
    """One club's entry in ``nhl.team_leaders``, ranked from its club-stats payload. A skater
    with none of a stat is not a leader in it, so a list can be shorter than the roster (and
    empty on opening night)."""
    skaters = _rows(club.get("skaters"))
    value: dict[str, Any] = {"season": _season(club.get("season")), "game_type": _int(club.get("gameType"))}
    for category in SKATER_CATEGORIES:
        ranked = sorted((s for s in skaters if (_int(s.get(category)) or 0) > 0), key=lambda s, c=category: (
            -(_int(s.get(c)) or 0), -(_int(s.get("points")) or 0), -(_int(s.get("goals")) or 0),
            _int(s.get("gamesPlayed")) or 0, _text(s.get("lastName"))))
        value[category] = [{**_who(s, s.get("playerId"), team), "position": _text(s.get("positionCode")),
                            "value": _int(s.get(category)) or 0} for s in ranked[:KEEP]]
    goalies = [{**_who(g, g.get("playerId"), team), "wins": _int(g.get("wins")) or 0,
                "save_pct": _float(g.get("savePercentage")), "gaa": _float(g.get("goalsAgainstAverage")),
                "shutouts": _int(g.get("shutouts")) or 0, "_gp": _int(g.get("gamesPlayed")) or 0}
               for g in _rows(club.get("goalies")) if (_int(g.get("gamesPlayed")) or 0) > 0]
    value["goalies"] = {}
    for stat, (_, _, lower) in GOALIE_STATS.items():
        def key(g: dict[str, Any], stat: str = stat, lower: bool = lower) -> tuple[Any, ...]:
            number = g[stat]
            if number is None:
                return (1, 0, -g["wins"], -g["_gp"], g["last"])   # no number: after everyone who has one
            return (0, number if lower else -number, -g["wins"], -g["_gp"], g["last"])
        value["goalies"][stat] = [{k: v for k, v in g.items() if k != "_gp"} for g in sorted(goalies, key=key)[:KEEP]]
    return value


def has_leaders(value: dict[str, Any] | None) -> bool:
    """Whether a league or club value has a single row to show."""
    if not value:
        return False
    return any(value.get(c) for c in SKATER_CATEGORIES) or any((value.get("goalies") or {}).values())


def _league_skater(row: dict[str, Any]) -> dict[str, Any]:
    return {**_who(row, row.get("id"), row.get("teamAbbrev")), "position": _text(row.get("position")),
            "value": _int(row.get("value")) or 0}


def _who(row: dict[str, Any], player_id: Any, team: Any) -> dict[str, Any]:
    return {"id": player_id, "first": _text(row.get("firstName")), "last": _text(row.get("lastName")),
            "team": _text(team).upper()}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _season(value: Any) -> str | None:
    return str(value) if value else None
