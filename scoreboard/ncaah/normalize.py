"""ESPN men's college hockey payloads -> the flat game dict the NHL boards consume.

ESPN's hockey scoreboard is the football one with a period clock: the side, score, rank and
season-type readers come from the NFL normaliser, and the hockey-specific parts (period and
overtime labels, the intermission state ESPN reports as ``STATUS_END_PERIOD``, shots on goal
worked back from the goalie statistics) live here. Records are not in the feed; the source
merges what it learns from the favourites' schedules (:func:`record_from_schedule`).
"""
from __future__ import annotations

from datetime import UTC, tzinfo
from typing import Any

from ..isotime import parse_iso
from ..nfl.normalize import _rank, _score, _season_type
from . import teams

PERIOD_LABELS = {1: "1st", 2: "2nd", 3: "3rd"}
INTERMISSION_STATUSES = frozenset({"STATUS_END_PERIOD", "STATUS_END_OF_PERIOD"})
NOT_PLAYED = {"STATUS_POSTPONED": ("PPD", "PPD"), "STATUS_CANCELED": ("CNCL", "CANCELLED"), "STATUS_CANCELLED": ("CNCL", "CANCELLED"),
              "STATUS_SUSPENDED": ("SUSP", "SUSPENDED")}


def _stat(c: dict[str, Any], name: str) -> int | None:
    for s in c.get("statistics") or []:
        if s.get("name") == name:
            try:
                return int(float(s.get("displayValue") or s.get("value") or 0))
            except (TypeError, ValueError):
                return None
    return None


def _side(c: dict[str, Any], records: dict[str, str] | None) -> dict[str, Any]:
    t = c.get("team") or {}
    abbrev = str(t.get("abbreviation") or "")
    teams.learn_colors(abbrev, t.get("color"), t.get("alternateColor"))
    primary, alt = teams.colors(abbrev)
    return {
        "id": str(t.get("id", "")), "abbrev": abbrev,
        "name": t.get("shortDisplayName") or t.get("location") or t.get("name", ""),      # the school, as a ticker says ALABAMA
        "city": t.get("location", ""), "score": _score(c.get("score")), "sog": 0,
        "record": (records or {}).get(abbrev, ""), "color": primary, "accent": alt, "rank": _rank(c),
    }


def period_label(period: int, shortdetail: str = "") -> str:
    if period <= 3:
        return PERIOD_LABELS.get(period, "")
    if period == 4:
        return "OT"
    return "SO" if "SO" in shortdetail.upper() or period >= 5 else "OT"


def normalize_game(event: dict[str, Any], records: dict[str, str] | None = None, tz: tzinfo | None = None) -> dict[str, Any] | None:
    """``tz`` is the viewer's zone: a game's ``date`` is its calendar day *there*, so a 04:00Z
    Friday puck drop on the east coast is still Thursday night in Ann Arbor."""
    comps = event.get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    status = comp.get("status") or event.get("status") or {}
    stype = status.get("type") or {}
    state, name = stype.get("state", "pre"), stype.get("name", "")
    short = stype.get("shortDetail") or stype.get("detail") or ""
    competitors = comp.get("competitors") or []
    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[0] if competitors else {})
    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[-1] if competitors else {})
    a, h = _side(away, records), _side(home, records)
    # ESPN's hockey statistics are the goalies': a side's shots are the other goalie's saves plus its own goals
    a_saves, h_saves = _stat(away, "saves"), _stat(home, "saves")
    if a_saves is not None and h_saves is not None:
        a["sog"], h["sog"] = h_saves + a["score"], a_saves + h["score"]
    period = int(status.get("period") or 0)
    intermission = state == "in" and name in INTERMISSION_STATUSES
    phase = {"pre": "pregame", "in": "intermission" if intermission else "live", "post": "postgame"}.get(state, "pregame")
    game_state = {"pre": "FUT", "in": "LIVE", "post": "OFF"}.get(state, "FUT")
    outcome = ""
    if state == "post":
        outcome = "FINAL/SO" if "SO" in short.upper() else "FINAL/OT" if period > 3 else "FINAL"
    schedule_state = "OK"
    if name in NOT_PLAYED:
        schedule_state, outcome = NOT_PLAYED[name]
        game_state, phase = schedule_state, "postgame"
    start = event.get("date", "")
    started = parse_iso(start)
    local_date = started.astimezone(tz or UTC).date().isoformat() if started is not None else ""
    return {
        "id": str(event.get("id", "")), "sport": "ncaah", "type": _season_type(event),
        "state": game_state, "schedule_state": schedule_state, "phase": phase,
        "date": local_date, "start_time_utc": start,
        "away": a, "home": h,
        "period": period_label(period, short), "period_number": period,
        "clock": status.get("displayClock", "") if state == "in" else "",
        "clock_running": state == "in" and not intermission, "in_intermission": intermission,
        "outcome": outcome,
        "powerplay": {"code": "ev", "clock": ""}, "pulled_goalie": 0, "goals": [], "penalties": [],
        "neutral_site": bool(comp.get("neutralSite")),
        "time_tbd": comp.get("timeValid", event.get("timeValid")) is False,     # ESPN parks a TBD start at 04:00Z; the boards print TBD
    }


def normalize_scoreboard(payload: dict[str, Any], records: dict[str, str] | None = None, tz: tzinfo | None = None) -> list[dict[str, Any]]:
    games = [normalize_game(e, records, tz) for e in payload.get("events") or []]
    return [g for g in games if g]


# -- team summary (from the club schedule: ESPN has no standings for this league) ----------


def schedule_games(schedule: dict[str, Any] | None, tz: tzinfo | None = None) -> list[dict[str, Any]]:
    games = [normalize_game(e, None, tz) for e in (schedule or {}).get("events") or []]
    return sorted((g for g in games if g), key=lambda g: g["start_time_utc"])


def record_from_schedule(games: list[dict[str, Any]], abbrev: str) -> dict[str, int]:
    """W-L-T from the games played, the way college hockey records read (an overtime loss is a loss;
    a shootout result counts as a tie in the overall record)."""
    wins = losses = ties = 0
    for g in games:
        if g["phase"] != "postgame" or g["outcome"] not in ("FINAL", "FINAL/OT", "FINAL/SO"):
            continue
        us, them = (g["home"], g["away"]) if g["home"]["abbrev"] == abbrev else (g["away"], g["home"])
        if g["outcome"] == "FINAL/SO" or us["score"] == them["score"]:
            ties += 1
        elif us["score"] > them["score"]:
            wins += 1
        else:
            losses += 1
    return {"wins": wins, "losses": losses, "ties": ties, "gp": wins + losses + ties}


def record_text(rec: dict[str, int]) -> str:
    return f"{rec['wins']}-{rec['losses']}-{rec['ties']}"


def _streak(games: list[dict[str, Any]], abbrev: str) -> str:
    played = [g for g in games if g["phase"] == "postgame"]
    if not played:
        return ""
    code, n = "", 0
    for g in reversed(played):
        r = _result(g, abbrev)
        if not code:
            code = r
        if r != code:
            break
        n += 1
    return f"{code}{n}" if code else ""


def _result(g: dict[str, Any], abbrev: str) -> str:
    us, them = (g["home"], g["away"]) if g["home"]["abbrev"] == abbrev else (g["away"], g["home"])
    if g["outcome"] == "FINAL/SO" or us["score"] == them["score"]:
        return "T"
    return "W" if us["score"] > them["score"] else "L"


def _rank_from(games: list[dict[str, Any]], abbrev: str, today: str) -> int | None:
    """The team's current poll rank, read off its nearest game (ESPN stamps ranks on competitors)."""
    from datetime import date

    def ordinal(day: str) -> int:
        try:
            return date.fromisoformat(day).toordinal()
        except ValueError:
            return 0
    ordered = sorted(games, key=lambda g: (g["date"] < today, abs(ordinal(g["date"]) - ordinal(today))))
    for g in ordered:
        for side in ("away", "home"):
            if g[side]["abbrev"] == abbrev:
                return g[side].get("rank")
    return None


def sched_entry(g: dict[str, Any] | None, abbrev: str) -> dict[str, Any] | None:
    if not g:
        return None
    is_home = g["home"]["abbrev"] == abbrev
    us, them = (g["home"], g["away"]) if is_home else (g["away"], g["home"])
    return {"id": g["id"], "date": g["date"], "start_time_utc": g["start_time_utc"], "home": is_home, "opponent": them["abbrev"],
            "score": us["score"], "opponent_score": them["score"],
            "result": _result(g, abbrev) if g["phase"] == "postgame" else "", "state": g["state"]}


def team_summary(abbrev: str, schedule: dict[str, Any] | None, today: str, tz: tzinfo | None = None) -> dict[str, Any]:
    games = schedule_games(schedule, tz)
    rec = record_from_schedule(games, abbrev)
    prev = next_game = None
    for g in games:
        if g["phase"] == "postgame":
            prev = g
        elif next_game is None and g["date"] >= today:
            next_game = g
    conf = teams.CONFERENCE_OF.get(abbrev, "")
    return {
        "abbrev": abbrev, "sport": "ncaah",
        "record": {"gp": rec["gp"], "points": 0, "wins": rec["wins"], "losses": rec["losses"], "otl": rec["ties"], "ties": rec["ties"],
                   "l10": [], "streak": _streak(games, abbrev), "division": conf, "conference": conf, "division_rank": 0,
                   "rank": _rank_from(games, abbrev, today)},
        "prev_game": sched_entry(prev, abbrev), "next_game": sched_entry(next_game, abbrev),
    }
