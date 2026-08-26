"""ESPN NFL payloads -> the same flat game dict shape the NHL boards use, plus NFL situation fields."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .teams import DIVISION_OF, colors, learn_colors

PERIOD_LABELS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}


def _record(competitor: dict[str, Any]) -> str:
    for r in competitor.get("records") or []:
        if r.get("type") == "total" or r.get("name") == "overall":
            return r.get("summary", "")
    return ""


def _score(v: Any) -> int:
    """Scoreboard gives a string/number; the schedule feed gives {'value': .., 'displayValue': ..}."""
    if isinstance(v, dict):
        v = v.get("value", v.get("displayValue"))
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _side(c: dict[str, Any]) -> dict[str, Any]:
    t = c.get("team") or {}
    learn_colors(t.get("abbreviation", ""), t.get("color"), t.get("alternateColor"))
    primary, alt = colors(t.get("abbreviation", ""))
    return {
        "id": str(t.get("id", "")), "abbrev": t.get("abbreviation", ""), "name": t.get("name", ""),
        "city": t.get("location", ""), "score": _score(c.get("score")), "sog": 0, "record": _record(c),
        "color": primary, "accent": alt, "timeouts": None,
    }


def period_label(period: int, state: str, status_name: str) -> str:
    if status_name == "STATUS_HALFTIME":
        return "HALF"
    if period > 4:
        return "OT" if period == 5 else f"{period - 4}OT"
    return PERIOD_LABELS.get(period, "")


def normalize_game(event: dict[str, Any]) -> dict[str, Any] | None:
    comps = event.get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    status = comp.get("status") or event.get("status") or {}
    stype = status.get("type") or {}
    state = stype.get("state", "pre")
    name = stype.get("name", "")
    competitors = comp.get("competitors") or []
    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[0] if competitors else {})
    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[-1] if competitors else {})
    a, h = _side(away), _side(home)
    period = int(status.get("period") or 0)
    halftime = name == "STATUS_HALFTIME"
    phase = {"pre": "pregame", "in": "intermission" if halftime else "live", "post": "postgame"}.get(state, "pregame")
    sit = comp.get("situation") or {}
    a["timeouts"], h["timeouts"] = sit.get("awayTimeouts"), sit.get("homeTimeouts")
    poss_id = str(sit.get("possession") or "")
    possession = "away" if poss_id and poss_id == a["id"] else "home" if poss_id and poss_id == h["id"] else None
    start = event.get("date", "")
    try:
        local_date = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(UTC).date().isoformat()
    except ValueError:
        local_date = ""
    outcome = ""
    if state == "post":
        outcome = "FINAL/OT" if period > 4 else "FINAL"
    return {
        "id": str(event.get("id", "")), "sport": "nfl", "type": _season_type(event),
        "week": ((event.get("week") or {}).get("number")),
        "state": state.upper() if state != "in" else ("HALF" if halftime else "LIVE"),
        "phase": phase, "date": local_date, "start_time_utc": start,
        "away": a, "home": h,
        "period": period_label(period, state, name), "period_number": period,
        "clock": status.get("displayClock", ""), "clock_running": state == "in" and not halftime,
        "in_intermission": halftime, "outcome": outcome,
        "powerplay": {"code": "ev", "clock": ""}, "pulled_goalie": 0, "goals": [], "penalties": [],
        "situation": {
            "possession": possession, "down": sit.get("down"), "distance": sit.get("distance"),
            "yard_line": sit.get("yardLine"), "red_zone": bool(sit.get("isRedZone")),
            "text": sit.get("shortDownDistanceText") or sit.get("downDistanceText") or "",
            "last_play": ((sit.get("lastPlay") or {}).get("text") or "")[:60],
        },
    }


def _season_type(event: dict[str, Any]) -> int:
    """Season type (1 pre, 2 regular, 3 post); the schedule feed nests it as a dict."""
    st = (event.get("season") or {}).get("type")
    if isinstance(st, dict):
        st = st.get("type") or st.get("id")
    try:
        return int(st or 2)
    except (TypeError, ValueError):
        return 2


def normalize_scoreboard(payload: dict[str, Any]) -> list[dict[str, Any]]:
    games = [normalize_game(e) for e in payload.get("events") or []]
    return [g for g in games if g]


def normalize_standings(payload: dict[str, Any]) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    for conf in payload.get("children") or []:
        conf_name = conf.get("abbreviation") or conf.get("name", "")
        for e in (conf.get("standings") or {}).get("entries") or []:
            t = e.get("team") or {}
            abbrev = t.get("abbreviation", "")
            stats = {s.get("name"): s for s in e.get("stats") or []}
            val = _stat_reader(stats)
            rows[abbrev] = {
                "abbrev": abbrev, "conference": conf_name, "division": DIVISION_OF.get(abbrev, ""),
                "gp": val("wins") + val("losses") + val("ties"), "wins": val("wins"), "losses": val("losses"), "otl": val("ties"),
                "points": val("wins") * 2 + val("ties"),      # so shared boards can sort; PTS column shows W-L-T instead
                "win_pct": stats.get("winPercent", {}).get("displayValue", ""), "streak": stats.get("streak", {}).get("displayValue", ""),
                "seed": val("playoffSeed"), "l10": [], "clinch": stats.get("clincher", {}).get("displayValue", "") if "clincher" in stats else "",
                "division_rank": 0, "conference_rank": val("playoffSeed"), "league_rank": 0, "wildcard_rank": 0,
            }
    by_div: dict[str, list[str]] = {}
    for div, teams in ((d, [t for t in ts if t in rows]) for d, ts in _divisions().items()):
        ordered = sorted(teams, key=lambda t: (-rows[t]["wins"], rows[t]["losses"], -rows[t]["otl"]))
        for i, t in enumerate(ordered, 1):
            rows[t]["division_rank"] = i
        by_div[div] = ordered
    league = sorted(rows, key=lambda t: (-rows[t]["wins"], rows[t]["losses"]))
    conf_groups: dict[str, dict[str, list[str]]] = {}
    for div, teams in by_div.items():
        conf_groups.setdefault(div.split()[0], {})[div] = teams
    return {"teams": rows, "division": by_div, "wildcard": conf_groups, "league": league}


def _stat_reader(stats: dict[str, Any]):
    def val(n: str, default: int = 0) -> int:
        v = stats.get(n, {}).get("value")
        return int(v) if isinstance(v, (int, float)) else default
    return val


def _divisions() -> dict[str, list[str]]:
    from .teams import DIVISIONS
    return DIVISIONS


def records_from_standings(standings: dict[str, Any] | None) -> dict[str, str]:
    out = {}
    for a, r in ((standings or {}).get("teams") or {}).items():
        out[a] = f"{r['wins']}-{r['losses']}" + (f"-{r['otl']}" if r["otl"] else "")
    return out


def team_summary(abbrev: str, standings: dict[str, Any] | None, schedule: dict[str, Any] | None, today: str) -> dict[str, Any]:
    row = ((standings or {}).get("teams") or {}).get(abbrev) or {}
    games = [normalize_game(e) for e in (schedule or {}).get("events") or []]
    games = sorted((g for g in games if g), key=lambda g: g["start_time_utc"])
    prev = next_game = None
    for g in games:
        if g["phase"] == "postgame":
            prev = g
        elif next_game is None and g["date"] >= today:
            next_game = g
    bye = (schedule or {}).get("byeWeek")
    return {
        "abbrev": abbrev, "sport": "nfl",
        "record": {"gp": row.get("gp", 0), "points": row.get("points", 0), "wins": row.get("wins", 0), "losses": row.get("losses", 0),
                   "otl": row.get("otl", 0), "l10": [], "streak": row.get("streak", ""), "division": row.get("division", ""),
                   "division_rank": row.get("division_rank", 0), "win_pct": row.get("win_pct", ""), "bye_week": bye},
        "prev_game": _sched(prev, abbrev), "next_game": _sched(next_game, abbrev),
    }


def _sched(g: dict[str, Any] | None, abbrev: str) -> dict[str, Any] | None:
    if not g:
        return None
    is_home = g["home"]["abbrev"] == abbrev
    us, them = (g["home"], g["away"]) if is_home else (g["away"], g["home"])
    result = ""
    if g["phase"] == "postgame":
        result = "W" if us["score"] > them["score"] else "L" if us["score"] < them["score"] else "T"
    return {"id": g["id"], "date": g["date"], "start_time_utc": g["start_time_utc"], "home": is_home, "opponent": them["abbrev"],
            "score": us["score"], "opponent_score": them["score"], "result": result, "state": g["state"], "week": g.get("week")}
