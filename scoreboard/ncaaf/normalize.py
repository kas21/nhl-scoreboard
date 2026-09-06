"""ESPN college football payloads -> the flat game dict the football boards share, plus
conference standings.

Games come from the NFL normaliser with the FBS colour registry and school names.
Standings differ: ESPN groups FBS by conference, and a conference that still plays
divisions nests them one level deeper, so the walk is recursive and a row carries both
``conference`` and ``division``. The ``division`` view on the standings board is one
conference per page; ``wildcard`` shows the divisions of the conferences that have them.
"""
from __future__ import annotations

from typing import Any

from ..nfl.normalize import normalize_game as _football_game
from ..nfl.normalize import prev_and_next, sched_entry, schedule_games
from . import teams

GAME_KW: dict[str, Any] = {"sport": "ncaaf", "teams": teams, "school_names": True}
CONF_RECORD_STATS = ("vsconf", "vs. conf.", "vsconference")


def normalize_game(event: dict[str, Any]) -> dict[str, Any] | None:
    return _football_game(event, **GAME_KW)


def normalize_scoreboard(payload: dict[str, Any]) -> list[dict[str, Any]]:
    games = [normalize_game(e) for e in payload.get("events") or []]
    return [g for g in games if g]


# -- standings ---------------------------------------------------------------------


def _label(node: dict[str, Any]) -> str:
    return str(node.get("abbreviation") or node.get("shortName") or node.get("name") or "")


def _groups(payload: dict[str, Any]):
    """Yield (conference, division, entries) for every standings table in the payload."""
    for conf in payload.get("children") or []:
        conf_label = _label(conf)
        kids = conf.get("children") or []
        if kids:
            for div in kids:
                div_label = str(div.get("name") or _label(div))
                for prefix in (conf.get("name") or "", conf_label):
                    if prefix and div_label.startswith(prefix):
                        div_label = div_label[len(prefix):].lstrip(" -\u2013")
                yield conf_label, div_label, (div.get("standings") or {}).get("entries") or []
        else:
            yield conf_label, "", (conf.get("standings") or {}).get("entries") or []


def _int(v: Any) -> int:
    return int(v) if isinstance(v, (int, float)) else 0


def _conf_record(stats: dict[str, dict[str, Any]]) -> tuple[int, int]:
    if "vsconf_wins" in stats or "vsconf_losses" in stats:
        return _int(stats.get("vsconf_wins", {}).get("value")), _int(stats.get("vsconf_losses", {}).get("value"))
    for name, s in stats.items():
        if name in CONF_RECORD_STATS or (s.get("type") or "").lower() == "vsconf":
            parts = str(s.get("displayValue") or "").split("-")
            if len(parts) >= 2 and all(p.isdigit() for p in parts[:2]):
                return int(parts[0]), int(parts[1])
    return 0, 0


def normalize_standings(payload: dict[str, Any]) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    members: dict[str, list[str]] = {}
    divisions: dict[str, dict[str, list[str]]] = {}
    for conf, div, entries in _groups(payload):
        for e in entries:
            t = e.get("team") or {}
            abbrev = str(t.get("abbreviation") or "")
            if not abbrev:
                continue
            stats = {str(s.get("name")).lower(): s for s in e.get("stats") or []}     # ESPN's college names are lowercase (winpercent), the NFL's camel
            wins, losses = _int(stats.get("wins", {}).get("value")), _int(stats.get("losses", {}).get("value"))
            cw, cl = _conf_record(stats)
            rows[abbrev] = {
                "abbrev": abbrev, "conference": conf, "division": div,
                "gp": wins + losses, "wins": wins, "losses": losses, "otl": 0, "points": wins * 2,
                "conf_wins": cw, "conf_losses": cl, "conf_record": f"{cw}-{cl}",
                "win_pct": stats.get("winpercent", {}).get("displayValue", ""), "streak": stats.get("streak", {}).get("displayValue", ""),
                "seed": 0, "l10": [], "clinch": "",
                "division_rank": 0, "conference_rank": 0, "league_rank": 0, "wildcard_rank": 0,
            }
            members.setdefault(conf, []).append(abbrev)
            if div:
                divisions.setdefault(conf, {}).setdefault(div, []).append(abbrev)

    def conf_key(a: str) -> tuple:
        r = rows[a]
        played = r["conf_wins"] + r["conf_losses"]
        return (-(r["conf_wins"] / played if played else 0.0), -r["conf_wins"], -r["wins"], r["losses"], a)

    by_conf: dict[str, list[str]] = {}
    for conf, abbrevs in members.items():
        ordered = sorted(abbrevs, key=conf_key)
        for i, a in enumerate(ordered, 1):
            rows[a]["conference_rank"] = rows[a]["division_rank"] = i
        by_conf[conf] = ordered
    for divs in divisions.values():
        for div, abbrevs in divs.items():
            divs[div] = sorted(abbrevs, key=conf_key)
            for i, a in enumerate(divs[div], 1):
                rows[a]["division_rank"] = i
    league = sorted(rows, key=lambda a: (-rows[a]["wins"], rows[a]["losses"], a))
    for i, a in enumerate(league, 1):
        rows[a]["league_rank"] = i
    return {"teams": rows, "division": by_conf, "wildcard": divisions, "league": league}


# -- team summary --------------------------------------------------------------------


def _rank_from(games: list[dict[str, Any]], abbrev: str, today: str) -> int | None:
    """The team's current poll rank, read off its nearest game (ESPN stamps ranks on competitors)."""
    ordered = sorted(games, key=lambda g: (g["date"] < today, abs((_ordinal(g["date"]) or 0) - (_ordinal(today) or 0))))
    for g in ordered:
        for side in ("away", "home"):
            if g[side]["abbrev"] == abbrev:
                return g[side].get("rank")
    return None


def _ordinal(day: str) -> int | None:
    from datetime import date
    try:
        return date.fromisoformat(day).toordinal()
    except ValueError:
        return None


def team_summary(abbrev: str, standings: dict[str, Any] | None, schedule: dict[str, Any] | None, today: str) -> dict[str, Any]:
    row = ((standings or {}).get("teams") or {}).get(abbrev) or {}
    games = schedule_games(schedule, **GAME_KW)
    prev, next_game = prev_and_next(games, today)
    return {
        "abbrev": abbrev, "sport": "ncaaf",
        "record": {"gp": row.get("gp", 0), "points": row.get("points", 0), "wins": row.get("wins", 0), "losses": row.get("losses", 0),
                   "otl": 0, "l10": [], "streak": row.get("streak", ""),
                   "division": row.get("conference", teams.CONFERENCE_OF.get(abbrev, "")), "conference": row.get("conference", ""),
                   "division_rank": row.get("division_rank", 0), "conference_rank": row.get("conference_rank", 0),
                   "conf_record": row.get("conf_record", ""), "win_pct": row.get("win_pct", ""),
                   "rank": _rank_from(games, abbrev, today)},
        "prev_game": sched_entry(prev, abbrev), "next_game": sched_entry(next_game, abbrev),
    }
