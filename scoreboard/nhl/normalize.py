"""Turn raw NHL API payloads into the flat, JSON-shaped dicts boards consume.

Everything here is a pure function of its inputs, so it is easy to test from
recorded fixtures and safe to call from any thread.
"""
from __future__ import annotations

from typing import Any

PHASE_BY_STATE = {
    "FUT": "pregame", "PRE": "pregame",
    "LIVE": "live", "CRIT": "live",
    "OVER": "postgame", "FINAL": "postgame", "OFF": "postgame",
}
ACTIVE_STATES = frozenset({"PRE", "LIVE", "CRIT"})
FINISHED_STATES = frozenset({"OVER", "FINAL", "OFF"})


def _text(value: Any) -> str:
    """NHL localised strings are ``{"default": "..."}``; tolerate plain strings."""
    if isinstance(value, dict):
        return str(value.get("default", ""))
    return "" if value is None else str(value)


def period_label(descriptor: dict[str, Any] | None, game_type: int | None) -> str:
    if not descriptor:
        return ""
    number = int(descriptor.get("number") or 0)
    ptype = descriptor.get("periodType", "REG")
    if ptype == "SO":
        return "SO"
    if ptype == "OT":
        ot_n = number - int(descriptor.get("maxRegulationPeriods") or 3)
        return f"{ot_n}OT" if game_type == 3 and ot_n > 1 else "OT"
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(number, f"{number}th")


def outcome_label(game: dict[str, Any]) -> str:
    """'' unless the game is over; then 'FINAL', 'FINAL/OT', 'FINAL/SO', 'FINAL/2OT'."""
    if game.get("gameState") not in FINISHED_STATES:
        return ""
    last = (game.get("gameOutcome") or {}).get("lastPeriodType") or (game.get("periodDescriptor") or {}).get("periodType")
    if last == "OT":
        n = (game.get("gameOutcome") or {}).get("otPeriods") or 1
        return f"FINAL/{n}OT" if n > 1 else "FINAL/OT"
    if last == "SO":
        return "FINAL/SO"
    return "FINAL"


def situation(code: str | None) -> tuple[str, int]:
    """Decode the 4-char situationCode (away goalie, away skaters, home skaters, home goalie).

    Returns (powerplay_code, pulled_goalie) where powerplay_code is 'ev',
    'a54' (away 5 on 4), 'h53' ... and pulled_goalie is 0 / 1 (away) / 2 (home) / 3 (both).
    """
    if not code or len(code) != 4 or not code.isdigit() or code in ("1010", "0101"):
        return "ev", 0
    ag, a_sk, h_sk, hg = (int(c) for c in code)
    pulled = (1 if ag == 0 else 0) | (2 if hg == 0 else 0)
    if a_sk > h_sk:
        return f"a{a_sk}{h_sk}", pulled
    if h_sk > a_sk:
        return f"h{h_sk}{a_sk}", pulled
    return "ev", pulled


def _team(raw: dict[str, Any], records: dict[str, str] | None) -> dict[str, Any]:
    abbrev = raw.get("abbrev", "")
    return {
        "abbrev": abbrev,
        "name": _text(raw.get("commonName") or raw.get("name")),
        "city": _text(raw.get("placeName")),
        "score": int(raw.get("score") or 0),
        "sog": int(raw.get("sog") or 0),
        "record": (records or {}).get(abbrev, ""),
    }


def _merge_team(score_team: dict[str, Any] | None, landing_team: dict[str, Any] | None) -> dict[str, Any]:
    """The score feed lacks city names; landing has them. Score wins for live numbers."""
    merged = dict(landing_team or {})
    merged.update(score_team or {})
    return merged


def _goal(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "team": _text(raw.get("teamAbbrev")),
        "period": int((raw.get("periodDescriptor") or {}).get("number") or raw.get("period") or 0),
        "time": raw.get("timeInPeriod", ""),
        "scorer": _text(raw.get("name")),
        "first_name": _text(raw.get("firstName")),
        "last_name": _text(raw.get("lastName")),
        "goals_to_date": int(raw.get("goalsToDate") or 0),
        "strength": raw.get("strength", "ev"),
        "assists": [_text(a.get("name")) for a in raw.get("assists") or []],
        "away_score": int(raw.get("awayScore") or 0),
        "home_score": int(raw.get("homeScore") or 0),
    }


def _penalties(landing: dict[str, Any] | None) -> list[dict[str, Any]]:
    out = []
    for period in ((landing or {}).get("summary") or {}).get("penalties") or []:
        number = int((period.get("periodDescriptor") or {}).get("number") or 0)
        for p in period.get("penalties") or []:
            out.append({
                "team": _text(p.get("teamAbbrev")),
                "period": number,
                "time": p.get("timeInPeriod", ""),
                "type": p.get("type", ""),
                "duration": int(p.get("duration") or 0),
                "desc": p.get("descKey", "").replace("-", " "),
                "player": _text(p.get("committedByPlayer") or p.get("servedBy")),
            })
    return out


def normalize_game(
    game: dict[str, Any],
    records: dict[str, str] | None = None,
    landing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Flatten a ``/score`` game (optionally enriched with its ``/landing``)."""
    state = game.get("gameState", "FUT")
    clock = game.get("clock") or {}
    in_intermission = bool(clock.get("inIntermission"))
    if state in ("LIVE", "CRIT") and not clock.get("running") and clock.get("timeRemaining") == "00:00":
        in_intermission = True
    phase = PHASE_BY_STATE.get(state, "pregame")
    if phase == "live" and in_intermission:
        phase = "intermission"
    sit = (landing or {}).get("situation") or game.get("situation") or {}
    pp_code, pulled = situation(sit.get("situationCode"))
    game_type = int(game.get("gameType") or 2)
    landing_goals = [g for per in ((landing or {}).get("summary") or {}).get("scoring") or [] for g in per.get("goals") or []]
    goals = [_goal(g) for g in (landing_goals or game.get("goals") or [])]
    return {
        "id": int(game["id"]),
        "type": game_type,
        "state": state,
        "phase": phase,
        "date": game.get("gameDate", ""),
        "start_time_utc": game.get("startTimeUTC", ""),
        "away": _team(_merge_team(game.get("awayTeam"), (landing or {}).get("awayTeam")), records),
        "home": _team(_merge_team(game.get("homeTeam"), (landing or {}).get("homeTeam")), records),
        "period": period_label(game.get("periodDescriptor"), game_type),
        "period_number": int((game.get("periodDescriptor") or {}).get("number") or 0),
        "clock": clock.get("timeRemaining", ""),
        "clock_running": bool(clock.get("running")),
        "in_intermission": in_intermission,
        "outcome": outcome_label(game),
        "powerplay": {"code": pp_code, "clock": sit.get("timeRemaining", "")},
        "pulled_goalie": pulled,
        "goals": goals,
        "penalties": _penalties(landing),
    }


def normalize_standings(payload: dict[str, Any]) -> dict[str, Any]:
    """{'teams': {abbrev: row}, 'division': {name: [abbrev...]}, 'wildcard': {conf: {...}}, 'league': [...]}"""
    rows = {}
    for raw in payload.get("standings") or []:
        abbrev = _text(raw.get("teamAbbrev"))
        rows[abbrev] = {
            "abbrev": abbrev,
            "conference": raw.get("conferenceName", ""),
            "division": raw.get("divisionName", ""),
            "gp": int(raw.get("gamesPlayed") or 0),
            "wins": int(raw.get("wins") or 0),
            "losses": int(raw.get("losses") or 0),
            "otl": int(raw.get("otLosses") or 0),
            "points": int(raw.get("points") or 0),
            "l10": [int(raw.get("l10Wins") or 0), int(raw.get("l10Losses") or 0), int(raw.get("l10OtLosses") or 0)],
            "streak": f"{raw.get('streakCode', '')}{raw.get('streakCount', '')}",
            "division_rank": int(raw.get("divisionSequence") or 0),
            "conference_rank": int(raw.get("conferenceSequence") or 0),
            "league_rank": int(raw.get("leagueSequence") or 0),
            "wildcard_rank": int(raw.get("wildcardSequence") or 0),
            "clinch": raw.get("clinchIndicator", ""),
        }
    by_div: dict[str, list[str]] = {}
    for r in sorted(rows.values(), key=lambda r: r["division_rank"]):
        by_div.setdefault(r["division"], []).append(r["abbrev"])
    wildcard: dict[str, dict[str, list[str]]] = {}
    for r in sorted(rows.values(), key=lambda r: (r["wildcard_rank"], r["division_rank"])):
        conf = wildcard.setdefault(r["conference"], {})
        bucket = r["division"] if r["wildcard_rank"] == 0 else "Wildcard"
        conf.setdefault(bucket, []).append(r["abbrev"])
    league = [r["abbrev"] for r in sorted(rows.values(), key=lambda r: r["league_rank"])]
    return {"teams": rows, "division": by_div, "wildcard": wildcard, "league": league}


def records_from_standings(standings: dict[str, Any] | None) -> dict[str, str]:
    return {a: f"{r['wins']}-{r['losses']}-{r['otl']}" for a, r in ((standings or {}).get("teams") or {}).items()}


def team_summary(abbrev: str, standings: dict[str, Any] | None, schedule: dict[str, Any] | None, today: str) -> dict[str, Any]:
    """Record block plus previous/next game from the club season schedule."""
    row = ((standings or {}).get("teams") or {}).get(abbrev) or {}
    games = (schedule or {}).get("games") or []
    prev = next_game = None
    for g in games:
        state = g.get("gameState")
        if state in FINISHED_STATES:
            prev = g
        elif next_game is None and g.get("gameDate", "") >= today:
            next_game = g
    return {
        "abbrev": abbrev,
        "record": {
            "gp": row.get("gp", 0), "points": row.get("points", 0),
            "wins": row.get("wins", 0), "losses": row.get("losses", 0), "otl": row.get("otl", 0),
            "l10": row.get("l10", [0, 0, 0]), "streak": row.get("streak", ""),
            "division": row.get("division", ""), "division_rank": row.get("division_rank", 0),
        },
        "prev_game": _sched_game(prev, abbrev),
        "next_game": _sched_game(next_game, abbrev),
    }


def _sched_game(g: dict[str, Any] | None, abbrev: str) -> dict[str, Any] | None:
    if not g:
        return None
    away, home = g.get("awayTeam") or {}, g.get("homeTeam") or {}
    is_home = home.get("abbrev") == abbrev
    us, them = (home, away) if is_home else (away, home)
    result = ""
    if g.get("gameState") in FINISHED_STATES:
        result = "W" if int(us.get("score") or 0) > int(them.get("score") or 0) else "L"
    return {
        "id": g.get("id"), "date": g.get("gameDate", ""), "start_time_utc": g.get("startTimeUTC", ""),
        "home": is_home, "opponent": them.get("abbrev", ""),
        "score": int(us.get("score") or 0), "opponent_score": int(them.get("score") or 0),
        "result": result, "state": g.get("gameState", ""),
    }
