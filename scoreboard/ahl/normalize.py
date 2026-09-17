"""HockeyTech AHL payloads -> the flat game dict the NHL boards consume.

The score bar is the slate: every game in a window of days with its live period, clock,
score and each side's record. The game-centre summary of the one game being followed adds
what the NHL's landing feed adds — goals with scorers and assists, penalties, shots — and,
since HockeyTech publishes no situation code, the power play is worked out from the penalty
log against the game clock (:func:`situation_from_summary`). Everything here is a pure
function of its inputs.
"""
from __future__ import annotations

from datetime import UTC, date, tzinfo
from typing import Any

from ..isotime import parse_iso
from .teams import CONFERENCE_OF_DIVISION, DIVISION_OF, full_name

# HockeyTech GameStatus: 1 scheduled, 2 in progress, 3 final (unofficial), 4 final.
STATE_BY_STATUS = {"1": "FUT", "2": "LIVE", "3": "OFF", "4": "OFF"}
PHASE_BY_STATE = {"FUT": "pregame", "LIVE": "live", "OFF": "postgame"}
NOT_PLAYED = {"postponed": ("PPD", "PPD"), "cancel": ("CNCL", "CANCELLED"), "suspend": ("SUSP", "SUSPENDED")}
PERIOD_LABELS = {"1": "1st", "2": "2nd", "3": "3rd"}
PERIOD_SECONDS = 1200
OT_SECONDS = {"regular": 300, "playoffs": 1200}
PENALTY_SKATERS_FLOOR = 3


def _utc(dt: Any) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else ""


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _record(row: dict[str, Any], side: str) -> str:
    wins, losses, otl, sol = (_int(row.get(f"{side}{k}")) for k in ("Wins", "RegulationLosses", "OTLosses", "ShootoutLosses"))
    if not any((wins, losses, otl, sol)) and not row.get(f"{side}Wins"):
        return ""
    return f"{wins}-{losses}-{otl + sol}"


def period_label(short: str) -> str:
    short = (short or "").strip()
    return PERIOD_LABELS.get(short, short.upper())


def _state(row: dict[str, Any]) -> tuple[str, str, str]:
    """(state, schedule_state, outcome) from the status code, with the status text as the tie-break
    for the states the code does not distinguish (a postponed game still says 1)."""
    text = str(row.get("GameStatusStringLong") or row.get("GameStatusString") or "")
    lowered = text.lower()
    for needle, (sched, outcome) in NOT_PLAYED.items():
        if needle in lowered:
            return sched, sched, outcome
    state = STATE_BY_STATUS.get(str(row.get("GameStatus")), "")
    if not state:
        state = "OFF" if "final" in lowered else "LIVE" if _int(row.get("Period")) and lowered and not lowered[0].isdigit() else "FUT"
    outcome = ""
    if state == "OFF":
        short = str(row.get("PeriodNameShort") or "").upper()
        outcome = "FINAL/SO" if short == "SO" else f"FINAL/{short}" if short.endswith("OT") else "FINAL"
    return state, "OK", outcome


def _side(row: dict[str, Any], side: str) -> dict[str, Any]:
    abbrev = str(row.get(f"{side}Code") or "").upper()
    return {
        "id": str(row.get(f"{side}ID") or ""), "abbrev": abbrev,
        "name": row.get(f"{side}Nickname") or full_name(abbrev), "city": row.get(f"{side}City") or "",
        "score": _int(row.get(f"{side}Goals")), "sog": 0, "record": _record(row, side),
    }


def _game_type(row: dict[str, Any], season_types: dict[str, int] | None) -> int:
    """1 preseason, 2 regular, 3 playoffs: from the seasons list when we have it, else from the game letter
    (``EX`` is an exhibition, a letter is a playoff series, nothing is the regular season)."""
    sid = str(row.get("SeasonID") or "")
    if season_types and sid in season_types:
        return season_types[sid]
    letter = str(row.get("game_letter") or "").upper()
    return 1 if letter == "EX" else 3 if letter else 2


def normalize_game(row: dict[str, Any], season_types: dict[str, int] | None = None, tz: tzinfo | None = None) -> dict[str, Any]:
    """Flatten a score-bar row. ``date`` is the league's own game date (``Date``), which is the
    local calendar day at the rink — what "tonight's games" means to the ticker."""
    state, schedule_state, outcome = _state(row)
    phase = PHASE_BY_STATE.get(state, "postgame")
    intermission = state == "LIVE" and str(row.get("Intermission")) == "1"
    if intermission:
        phase = "intermission"
    start = parse_iso(str(row.get("GameDateISO8601") or ""))
    return {
        "id": _int(row.get("ID")), "sport": "ahl", "type": _game_type(row, season_types),
        "state": state, "schedule_state": schedule_state, "phase": phase,
        "date": str(row.get("Date") or ""), "start_time_utc": _utc(start),
        "away": _side(row, "Visitor"), "home": _side(row, "Home"),
        "period": period_label(row.get("PeriodNameShort")) if state != "FUT" else "",
        "period_number": _int(row.get("Period")) if state != "FUT" else 0,
        "clock": str(row.get("GameClock") or "") if state == "LIVE" else "",
        "clock_running": state == "LIVE" and not intermission, "in_intermission": intermission,
        "outcome": outcome,
        "powerplay": {"code": "ev", "clock": ""}, "pulled_goalie": 0, "goals": [], "penalties": [],
        "venue": row.get("venue_name") or "",
    }


def normalize_scorebar(payload: dict[str, Any], season_types: dict[str, int] | None = None, tz: tzinfo | None = None) -> list[dict[str, Any]]:
    games = [normalize_game(r, season_types, tz) for r in payload.get("Scorebar") or []]
    return sorted(games, key=lambda g: (g["date"], g["start_time_utc"]))


# -- game summary enrichment (the followed game only) ------------------------------------


def _player(p: dict[str, Any] | None) -> str:
    p = p or {}
    return " ".join(str(p.get(k) or "") for k in ("first_name", "last_name")).strip()


def _seconds(clock: str) -> int:
    """``H:MM:SS`` or ``MM:SS`` -> seconds."""
    parts = [p for p in str(clock or "").split(":") if p.strip() != ""]
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0
    secs = 0
    for n in nums:
        secs = secs * 60 + n
    return secs


def _mmss(secs: int) -> str:
    secs = max(int(secs), 0)
    return f"{secs // 60:02d}:{secs % 60:02d}"


def _goal(raw: dict[str, Any], sides: dict[str, str], running: dict[str, int]) -> dict[str, Any]:
    team = sides.get(str(raw.get("team_id")), "")
    running[team] = running.get(team, 0) + 1
    strength = "pp" if str(raw.get("power_play")) == "1" else "sh" if str(raw.get("short_handed")) == "1" else "ev"
    scorer = raw.get("goal_scorer") or {}
    assists = [_player(raw.get(k)) for k in ("assist1_player", "assist2_player")]
    return {
        "team": team, "period": _int(raw.get("period_id")), "time": str(raw.get("time") or ""),
        "scorer": _player(scorer), "first_name": scorer.get("first_name", ""), "last_name": scorer.get("last_name", ""),
        "sweater": scorer.get("jersey_number", ""), "goals_to_date": _int(raw.get("scorer_goal_num")),
        "strength": strength, "empty_net": str(raw.get("empty_net")) == "1",
        "assists": [a for a in assists if a],
        "away_score": running.get("__away", 0), "home_score": running.get("__home", 0),
    }


def _penalty(raw: dict[str, Any], sides: dict[str, str]) -> dict[str, Any]:
    who = raw.get("player_penalized_info") or raw.get("player_served_info") or {}
    return {
        "team": sides.get(str(raw.get("team_id")), ""), "period": _int(raw.get("period_id")),
        "time": str(raw.get("time_off_formatted") or ""), "type": str(raw.get("penalty_class") or "").upper(),
        "duration": _int(raw.get("minutes")), "desc": str(raw.get("lang_penalty_description") or ""),
        "player": _player(who), "sweater": who.get("jersey_number", ""),
        "bench": str(raw.get("bench")) == "1", "penalty_shot": str(raw.get("penalty_shot")) == "1",
        "elapsed": _int(raw.get("s")),          # seconds into its period

    }


def _period_length(summary: dict[str, Any], period: int, game_type: int) -> int:
    periods = summary.get("periods") or {}
    entry = periods.get(str(period)) if isinstance(periods, dict) else None
    if isinstance(entry, dict) and _int(entry.get("length")):
        return _int(entry.get("length"))
    return PERIOD_SECONDS if period <= 3 else OT_SECONDS["playoffs" if game_type == 3 else "regular"]


def elapsed_seconds(summary: dict[str, Any], period: int, clock: str, game_type: int) -> int:
    """Game seconds elapsed at ``clock`` (time remaining) in ``period``."""
    before = sum(_period_length(summary, p, game_type) for p in range(1, max(period, 1)))
    return before + max(_period_length(summary, period, game_type) - _seconds(clock), 0)


def _game_seconds(summary: dict[str, Any], period: int, secs_in_period: int, game_type: int) -> int:
    """HockeyTech's ``s`` on a goal or penalty counts from the start of *its period*."""
    return sum(_period_length(summary, p, game_type) for p in range(1, max(period, 1))) + secs_in_period


def situation_from_summary(summary: dict[str, Any], game: dict[str, Any], sides: dict[str, str]) -> tuple[str, str]:
    """(powerplay code, time left on it) from the penalty log and the goals, the NHL feed's ``situation`` reconstructed.

    A minor runs its minutes from the moment it was called, or until the other side scores on the
    power play (the goal ends the minor that started first); a major runs its full five; coincidental
    minors take a skater from each side and cancel out. Skaters never drop below three a side. Only
    meaningful while the game is live.
    """
    if game["phase"] not in ("live", "intermission"):
        return "ev", ""
    game_type = game.get("type", 2)
    now = elapsed_seconds(summary, game["period_number"], game["clock"], game_type)
    pens: list[dict[str, Any]] = []
    for raw in summary.get("penalties") or []:
        pen = _penalty(raw, sides)
        if pen["penalty_shot"] or pen["duration"] not in (2, 4, 5):
            continue                                            # misconducts keep the skater count
        start = _game_seconds(summary, pen["period"], pen["elapsed"], game_type)
        pens.append({"team": pen["team"], "start": start, "end": start + pen["duration"] * 60, "minor": pen["duration"] in (2, 4)})
    pens.sort(key=lambda p: p["start"])
    goals = sorted(summary.get("goals") or [], key=lambda g: (_int(g.get("period_id")), _int(g.get("s"))))
    for g in goals:
        if str(g.get("power_play")) != "1":
            continue
        gt, gteam = _game_seconds(summary, _int(g.get("period_id")), _int(g.get("s")), game_type), sides.get(str(g.get("team_id")), "")
        if gt > now:
            break
        victim = next((p for p in pens if p["minor"] and p["team"] and p["team"] != gteam and p["start"] <= gt < p["end"]), None)
        if victim is not None:
            victim["end"] = gt
    active = [p for p in pens if p["start"] <= now < p["end"]]
    away, home = game["away"]["abbrev"], game["home"]["abbrev"]
    a_pen = [p for p in active if p["team"] == away]
    h_pen = [p for p in active if p["team"] == home]
    a_sk, h_sk = max(5 - len(a_pen), PENALTY_SKATERS_FLOOR), max(5 - len(h_pen), PENALTY_SKATERS_FLOOR)
    if a_sk == h_sk:
        return "ev", ""
    short = a_pen if a_sk < h_sk else h_pen
    left = min(p["end"] for p in short) - now
    return (f"h{h_sk}{a_sk}" if a_sk < h_sk else f"a{a_sk}{h_sk}"), _mmss(left)


def enrich_from_summary(game: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    """The followed game with goals, penalties, shots and the power play from its game-centre summary."""
    if not summary:
        return game
    ids = {str((summary.get("visitor") or {}).get("id") or ""): game["away"]["abbrev"],
           str((summary.get("home") or {}).get("id") or ""): game["home"]["abbrev"]}
    running: dict[str, int] = {}
    goals = []
    for raw in sorted(summary.get("goals") or [], key=lambda g: (_int(g.get("period_id")), _int(g.get("s")))):
        g = _goal(raw, ids, running)
        running["__away"] = running.get(game["away"]["abbrev"], 0)
        running["__home"] = running.get(game["home"]["abbrev"], 0)
        g["away_score"], g["home_score"] = running["__away"], running["__home"]
        goals.append(g)
    penalties = [_penalty(p, ids) for p in sorted(summary.get("penalties") or [], key=lambda p: (_int(p.get("period_id")), _int(p.get("s"))))]
    shots = summary.get("totalShots") or {}
    code, clock = situation_from_summary(summary, game, ids)
    return {
        **game,
        "away": {**game["away"], "sog": _int(shots.get("visitor"))},
        "home": {**game["home"], "sog": _int(shots.get("home"))},
        "goals": goals, "penalties": penalties,
        "powerplay": {"code": code, "clock": clock},
    }


# -- standings ------------------------------------------------------------------------


def _clean_code(code: str) -> tuple[str, str]:
    """``"y - PRO"`` -> ``("PRO", "y")``: the clinch letter rides in front of the code."""
    text = str(code or "").strip()
    if " - " in text:
        clinch, _, abbrev = text.partition(" - ")
        return abbrev.strip().upper(), clinch.strip()
    return text.upper(), ""


def _streak(text: str) -> str:
    """``"0-2-0-0"`` (W-L-OTL-SOL) -> ``"L2"``."""
    parts = [_int(p) for p in str(text or "").split("-")]
    labels = ("W", "L", "OTL", "SOL")
    for label, n in zip(labels, parts):
        if n:
            return f"{label}{n}"
    return ""


def _past10(text: str) -> list[int]:
    parts = [_int(p) for p in str(text or "").split("-")]
    parts += [0] * (4 - len(parts))
    return [parts[0], parts[1], parts[2] + parts[3]]


def normalize_standings(payload: list[dict[str, Any]]) -> dict[str, Any]:
    """{'teams': {abbrev: row}, 'division': {name: [abbrev...]}, 'wildcard': {conf: {div: [...]}}, 'league': [...]}

    The AHL seeds its playoffs by division, so ``wildcard`` is just each conference's divisions."""
    rows: dict[str, dict[str, Any]] = {}
    by_div: dict[str, list[str]] = {}
    for block in payload or []:
        for section in block.get("sections") or []:
            headers = section.get("headers") or {}
            division = str(((headers.get("team_code") or {}).get("properties") or {}).get("label") or section.get("title") or "").strip()
            division = division.replace(" Division", "")
            for entry in section.get("data") or []:
                r = entry.get("row") or {}
                abbrev, clinch = _clean_code(r.get("team_code"))
                if not abbrev:
                    continue
                div = division or DIVISION_OF.get(abbrev, "")
                otl = _int(r.get("ot_losses")) + _int(r.get("shootout_losses"))
                rows[abbrev] = {
                    "abbrev": abbrev, "conference": CONFERENCE_OF_DIVISION.get(div, ""), "division": div,
                    "gp": _int(r.get("games_played")), "wins": _int(r.get("wins")), "losses": _int(r.get("losses")),
                    "otl": otl, "points": _int(r.get("points")), "win_pct": str(r.get("percentage") or ""),
                    "l10": _past10(r.get("past_10")), "streak": _streak(r.get("streak")),
                    "division_rank": _int(r.get("rank")), "conference_rank": 0, "league_rank": _int(r.get("overall_rank")),
                    "wildcard_rank": 0, "clinch": clinch,
                }
                by_div.setdefault(div, []).append(abbrev)
    for div, teams in by_div.items():
        by_div[div] = sorted(teams, key=lambda a: (rows[a]["division_rank"] or 99, -rows[a]["points"]))
    wildcard: dict[str, dict[str, list[str]]] = {}
    for div, teams in by_div.items():
        wildcard.setdefault(CONFERENCE_OF_DIVISION.get(div, div), {})[div] = teams
    for divs in wildcard.values():
        ranked = sorted((a for teams in divs.values() for a in teams), key=lambda a: (-rows[a]["points"], -float(rows[a]["win_pct"] or 0)))
        for i, a in enumerate(ranked, 1):
            rows[a]["conference_rank"] = i
    league = sorted(rows, key=lambda a: (rows[a]["league_rank"] or 99, -rows[a]["points"]))
    return {"teams": rows, "division": by_div, "wildcard": wildcard, "league": league}


def records_from_standings(standings: dict[str, Any] | None) -> dict[str, str]:
    return {a: f"{r['wins']}-{r['losses']}-{r['otl']}" for a, r in ((standings or {}).get("teams") or {}).items()}


# -- club schedule -> team summary ----------------------------------------------------


def _sched_game(g: dict[str, Any], abbrev: str) -> dict[str, Any]:
    is_home = str(g.get("home_team_code") or "").upper() == abbrev
    us, them = ("home", "visiting") if is_home else ("visiting", "home")
    final = str(g.get("final")) == "1" or str(g.get("status")) in ("3", "4")
    ours, theirs = _int(g.get(f"{us}_goal_count")), _int(g.get(f"{them}_goal_count"))
    start = parse_iso(str(g.get("GameDateISO8601") or g.get("date_time_played") or ""))
    return {
        "id": _int(g.get("game_id") or g.get("id")), "date": str(g.get("date_played") or ""),
        "start_time_utc": _utc(start),
        "home": is_home, "opponent": str(g.get(f"{them}_team_code") or "").upper(),
        "score": ours, "opponent_score": theirs,
        "result": ("W" if ours > theirs else "L") if final else "", "state": "OFF" if final else "FUT",
    }


def team_summary(abbrev: str, standings: dict[str, Any] | None, schedule: dict[str, Any] | None, today: str) -> dict[str, Any]:
    """Record block plus previous/next game from the club's season schedule."""
    abbrev = abbrev.upper()
    row = ((standings or {}).get("teams") or {}).get(abbrev) or {}
    games = sorted((_sched_game(g, abbrev) for g in (schedule or {}).get("Schedule") or []), key=lambda g: (g["date"], g["start_time_utc"]))
    prev = next_game = None
    for g in games:
        if g["state"] == "OFF":
            prev = g
        elif next_game is None and g["date"] >= today:
            next_game = g
    return {
        "abbrev": abbrev, "sport": "ahl",
        "record": {
            "gp": row.get("gp", 0), "points": row.get("points", 0),
            "wins": row.get("wins", 0), "losses": row.get("losses", 0), "otl": row.get("otl", 0),
            "l10": row.get("l10", [0, 0, 0]), "streak": row.get("streak", ""),
            "division": row.get("division", DIVISION_OF.get(abbrev, "")), "division_rank": row.get("division_rank", 0),
        },
        "prev_game": prev, "next_game": next_game,
    }


# -- seasons ---------------------------------------------------------------------------


def _date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def season_kind(s: dict[str, Any]) -> str:
    """preseason | regular | playoffs | other (all-star) for a row of the seasons feed."""
    name = str(s.get("season_name") or "").lower()
    if str(s.get("playoff")) == "1":
        return "playoffs"
    if "preseason" in name or "exhibition" in name:
        return "preseason"
    if str(s.get("career")) == "1" or "regular" in name:
        return "regular"
    return "other"


def season_types(seasons: dict[str, Any]) -> dict[str, int]:
    """season_id -> game type (1 pre, 2 regular, 3 playoffs), for the score-bar normaliser."""
    out = {}
    for s in seasons.get("Seasons") or []:
        kind = season_kind(s)
        out[str(s.get("season_id"))] = {"preseason": 1, "playoffs": 3}.get(kind, 2)
    return out


def _season_id_number(s: dict[str, Any]) -> int | None:
    """``2025-26`` -> 20252026, the NHL's season-id shape the standings banner formats."""
    short = str(s.get("shortname") or "")
    if "-" in short and short[:4].isdigit():
        return int(short[:4]) * 10000 + int(short[:4][:2] + short.split("-")[1][-2:])
    start = _date(s.get("start_date"))
    return start.year * 10000 + start.year + 1 if start else None


def pick_seasons(seasons: dict[str, Any], today: date) -> dict[str, Any]:
    """Which seasons matter today: the regular season for standings and schedules (the one in progress,
    else the last one played, and the next one for the schedule when it is yet to start), plus the phase."""
    rows = sorted((s for s in seasons.get("Seasons") or [] if _date(s.get("start_date"))), key=lambda s: _date(s["start_date"]))
    regular = [s for s in rows if season_kind(s) == "regular"]
    playoffs = [s for s in rows if season_kind(s) == "playoffs"]
    pre = [s for s in rows if season_kind(s) == "preseason"]

    def within(s: dict[str, Any]) -> bool:
        a, b = _date(s.get("start_date")), _date(s.get("end_date"))
        return bool(a and b and a <= today <= b)

    current_regular = next((s for s in regular if within(s)), None)
    past_regular = [s for s in regular if (_date(s["start_date"]) or today) <= today]
    future_regular = [s for s in regular if (_date(s["start_date"]) or today) > today]
    standings_season = current_regular or (past_regular[-1] if past_regular else None)
    schedule_season = current_regular or (future_regular[0] if future_regular else standings_season)
    current_playoffs = next((s for s in playoffs if within(s)), None)
    current_pre = next((s for s in pre if within(s)), None)
    upcoming_pre = next((s for s in pre if (_date(s["start_date"]) or today) > today), None)
    if current_regular:
        phase = "regular"
    elif current_playoffs:
        phase = "playoffs"
    elif current_pre:
        phase = "preseason"
    else:
        phase = "offseason"
    next_regular = future_regular[0] if future_regular else None
    pre_start = _date((current_pre or upcoming_pre or {}).get("start_date"))
    reg_start = _date((current_regular or next_regular or {}).get("start_date"))
    season_num = _season_id_number(schedule_season) if schedule_season else None
    standings_num = _season_id_number(standings_season) if standings_season else None
    return {
        "phase": phase,
        "standings_season_id": _int((standings_season or {}).get("season_id")) or None,
        "schedule_season_id": _int((schedule_season or {}).get("season_id")) or None,
        "info": {
            "sport": "ahl", "phase": phase, "season_id": season_num,
            "preseason_start": pre_start.isoformat() if pre_start else None,
            "regular_start": reg_start.isoformat() if reg_start else None,
            "regular_end": (_date((current_regular or next_regular or {}).get("end_date")) or date.min).isoformat() if (current_regular or next_regular) else None,
            "playoff_end": (_date((current_playoffs or {}).get("end_date")) or date.min).isoformat() if current_playoffs else None,
            "days_to_preseason": (pre_start - today).days if pre_start and pre_start >= today else None,
            "days_to_regular": (reg_start - today).days if reg_start and reg_start >= today else None,
            "standings_season_id": standings_num,
            "standings_final": bool(standings_num and season_num and standings_num < season_num),
            "first_game": None,
        },
    }
