"""MLB Stats API payloads -> the flat game dict the shared boards use, plus baseball situation fields.

Everything here is a pure function of its inputs (recorded fixtures drive the tests).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from .teams import DIVISION_OF, DIVISION_ORDER, LEAGUE_OF, abbrev_for, team

SUFFIXES = {1: "st", 2: "nd", 3: "rd"}
HALF_LABELS = {"top": "TOP", "bottom": "BOT", "middle": "MID", "end": "END"}
# gameType -> season type (1 pre / 2 regular / 3 playoffs) as the shared boards expect
SEASON_TYPES = {"S": 1, "E": 1, "A": 1, "R": 2, "F": 3, "D": 3, "L": 3, "W": 3}
SERIES_LABELS = {"S": "SPRING", "E": "EXHIBITION", "A": "ALL-STAR", "F": "WILD CARD", "D": "DS", "L": "CS", "W": "WORLD SERIES"}
PLAY_LABELS = {
    "single": "1B", "double": "2B", "triple": "3B", "home_run": "HOME RUN", "walk": "BB", "intent_walk": "IBB",
    "hit_by_pitch": "HBP", "strikeout": "K", "strike_out": "K", "strikeout_double_play": "K",
    "field_error": "E", "error": "E", "fielders_choice": "FC", "fielders_choice_out": "FC",
    "grounded_into_double_play": "DP", "double_play": "DP", "triple_play": "TP", "sac_fly": "SAC FLY", "sac_bunt": "SAC",
    "field_out": "OUT", "force_out": "OUT", "catcher_interf": "CI", "stolen_base_2b": "SB", "stolen_base_3b": "SB",
    "stolen_base_home": "SB", "caught_stealing_2b": "CS", "caught_stealing_3b": "CS", "pickoff_1b": "PO", "pickoff_2b": "PO",
    "wild_pitch": "WP", "passed_ball": "PB", "balk": "BALK",
}
PITCH_LABELS = {
    "FF": "4-SEAM", "FA": "FASTBALL", "FT": "2-SEAM", "SI": "SINKER", "FC": "CUTTER", "SL": "SLIDER", "ST": "SWEEPER",
    "SV": "SLURVE", "CU": "CURVE", "KC": "KN-CURVE", "CS": "SLOW CURVE", "CH": "CHANGE", "FS": "SPLITTER", "FO": "FORK",
    "KN": "KNUCKLE", "EP": "EEPHUS", "SC": "SCREW", "GY": "GYRO", "PO": "PITCHOUT", "IN": "INT BALL", "AB": "AUTO BALL",
    "AS": "AUTO STRIKE", "NP": "NO PITCH", "UN": "",
}


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else SUFFIXES.get(n % 10, "th")
    return f"{n}{suffix}"


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def last_name(full: str) -> str:
    """'Shohei Ohtani' -> 'Ohtani'; keeps suffix-less last token, drops Jr./II/III."""
    parts = [p for p in (full or "").replace(".", "").split() if p.lower() not in ("jr", "sr", "ii", "iii", "iv")]
    return parts[-1] if parts else ""


def _name(obj: Any) -> str:
    return (obj or {}).get("fullName", "") if isinstance(obj, dict) else ""


def _side(raw: dict[str, Any], line_team: dict[str, Any]) -> dict[str, Any]:
    t = raw.get("team") or {}
    abbrev = abbrev_for(t.get("id"), t.get("abbreviation"))
    info = team(abbrev)
    rec = raw.get("leagueRecord") or {}
    record = f"{rec['wins']}-{rec['losses']}" if "wins" in rec and "losses" in rec else ""
    score = raw.get("score")
    if score is None:
        score = line_team.get("runs")
    return {
        "id": str(t.get("id", "")), "abbrev": abbrev, "name": info.name or t.get("teamName", ""), "city": info.city,
        "score": _int(score), "sog": 0, "hits": _int(line_team.get("hits")), "errors": _int(line_team.get("errors")),
        "record": record, "color": info.primary, "accent": info.accent,
        "probable_pitcher": _name(raw.get("probablePitcher")),
    }


def game_state(status: dict[str, Any]) -> tuple[str, str, str]:
    """(state, phase, outcome-ish label) from the Stats API status block.

    Warmup and pre-game count as pregame (no innings yet) even though the feed calls them
    live; inning breaks stay ``live`` (17 two-minute "intermissions" would thrash the playlist).
    """
    detailed = status.get("detailedState") or ""
    abstract = status.get("abstractGameState") or ""
    head = detailed.split(":")[0].strip()
    if head == "Postponed":
        return "PPD", "postgame", "PPD"
    if head == "Cancelled":
        return "CANCELLED", "postgame", "CANCELLED"
    if head == "Suspended":
        return "SUSP", "postgame", "SUSPENDED"
    if head == "Forfeit":
        return "FINAL", "postgame", "FORFEIT"
    if abstract == "Final" or head in ("Final", "Game Over", "Completed Early"):
        return "FINAL", "postgame", "FINAL"
    if head in ("Scheduled", "Delayed Start", "Pre-Game", "Warmup") or abstract == "Preview":
        return ("FUT" if head == "Scheduled" else "PRE"), "pregame", ""
    return "LIVE", "live", ""


def _delay(status: dict[str, Any]) -> str:
    detailed = status.get("detailedState") or ""
    head, _, reason = detailed.partition(":")
    if head.strip() in ("Delayed", "Delayed Start", "Suspended"):
        reason = (reason or status.get("reason") or "").strip()
        return f"{reason} DELAY".strip().upper() if head.strip() != "Suspended" else "SUSPENDED"
    return ""


def _situation(line: dict[str, Any], status: dict[str, Any], phase: str) -> dict[str, Any]:
    inning = _int(line.get("currentInning"))
    state = (line.get("inningState") or ("Top" if line.get("isTopInning", True) else "Bottom")).lower()
    offense, defense = line.get("offense") or {}, line.get("defense") or {}
    batting = "away" if line.get("isTopInning", state in ("top", "end")) else "home"
    return {
        "inning": inning, "inning_ordinal": (line.get("currentInningOrdinal") or ordinal(inning) if inning else "").upper(),
        "half": state if state in HALF_LABELS else "top", "scheduled_innings": _int(line.get("scheduledInnings"), 9),
        "batting": batting if phase == "live" else None,
        "balls": _int(line.get("balls")), "strikes": _int(line.get("strikes")), "outs": _int(line.get("outs")),
        "runners": [bool(offense.get(b)) for b in ("first", "second", "third")],
        "batter": _name(offense.get("batter")), "on_deck": _name(offense.get("onDeck")), "in_hole": _name(offense.get("inHole")),
        "pitcher": _name(defense.get("pitcher")), "pitcher_id": (defense.get("pitcher") or {}).get("id"),
        "last_play": None, "pitch": None, "pitch_count": None, "no_hitter": False, "perfect_game": False,
        "delay": _delay(status), "note": line.get("note") or "",
    }


def series_label(game: dict[str, Any], home_abbrev: str) -> str:
    gt = game.get("gameType") or "R"
    label = SERIES_LABELS.get(gt, "")
    if gt in ("D", "L"):
        label = f"{LEAGUE_OF.get(home_abbrev, '')}{label}"          # ALDS / NLCS
    if game.get("doubleHeader") in ("Y", "S") and game.get("gameNumber"):
        label = f"{label} GM{game['gameNumber']}".strip()
    return label


def normalize_game(game: dict[str, Any]) -> dict[str, Any] | None:
    teams = game.get("teams") or {}
    if "away" not in teams or "home" not in teams:
        return None
    line = game.get("linescore") or {}
    line_teams = line.get("teams") or {}
    status = game.get("status") or {}
    state, phase, outcome = game_state(status)
    away, home = _side(teams["away"], line_teams.get("away") or {}), _side(teams["home"], line_teams.get("home") or {})
    sit = _situation(line, status, phase)
    inning = sit["inning"]
    if outcome == "FINAL" and inning and inning != sit["scheduled_innings"]:
        outcome = f"FINAL/{inning}"
    start = game.get("gameDate") or ""
    local_date = game.get("officialDate") or ""
    if not local_date:
        try:
            local_date = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(UTC).date().isoformat()
        except ValueError:
            local_date = ""
    decisions = game.get("decisions") or {}
    period = HALF_LABELS.get(sit["half"], "") if phase == "live" else ""
    return {
        "id": str(game.get("gamePk", "")), "sport": "mlb", "type": SEASON_TYPES.get(game.get("gameType") or "R", 2),
        "game_type": game.get("gameType") or "R", "series": series_label(game, home["abbrev"]),
        "state": state, "phase": phase, "date": local_date, "start_time_utc": start,
        "start_tbd": bool(status.get("startTimeTBD")), "venue": (game.get("venue") or {}).get("name", ""),
        "away": away, "home": home,
        "period": period, "period_number": inning, "clock": sit["inning_ordinal"] if phase == "live" else "",
        "clock_running": phase == "live" and sit["half"] in ("top", "bottom"), "in_intermission": False, "outcome": outcome,
        "powerplay": {"code": "ev", "clock": ""}, "pulled_goalie": 0, "goals": [], "penalties": [],
        "situation": sit,
        "decisions": {k: _name(decisions.get(k)) for k in ("winner", "loser", "save")},
    }


def normalize_schedule(payload: dict[str, Any]) -> list[dict[str, Any]]:
    games = [normalize_game(g) for d in payload.get("dates") or [] for g in d.get("games") or []]
    return sorted((g for g in games if g), key=lambda g: (g["start_time_utc"], g["id"]))


# -- live feed enrichment -----------------------------------------------------------


def enrich_from_feed(game: dict[str, Any], feed: dict[str, Any]) -> dict[str, Any]:
    """Merge what only the live feed knows: last play, last pitch, pitch count, no-hitter flags, decisions."""
    live = feed.get("liveData") or {}
    sit = dict(game["situation"])
    play = (live.get("plays") or {}).get("currentPlay") or {}
    result, about = play.get("result") or {}, play.get("about") or {}
    if result.get("eventType"):
        etype = result["eventType"]
        sit["last_play"] = {
            "type": etype, "label": PLAY_LABELS.get(etype, (result.get("event") or "").upper()[:10]),
            "text": result.get("description") or "", "complete": bool(about.get("isComplete")),
            "rbi": _int(result.get("rbi")), "inning": _int(about.get("inning")),
            "batting": "away" if about.get("isTopInning", True) else "home",
        }
    pitches = [e for e in play.get("playEvents") or [] if e.get("isPitch")]
    if pitches:
        last = pitches[-1]
        code = ((last.get("details") or {}).get("type") or {}).get("code") or "UN"
        speed = (last.get("pitchData") or {}).get("startSpeed")
        sit["pitch"] = {"speed": round(float(speed)) if speed else None, "code": code,
                        "label": PITCH_LABELS.get(code, code), "call": ((last.get("details") or {}).get("call") or {}).get("description", "")}
    flags = (feed.get("gameData") or {}).get("flags") or {}
    sit["no_hitter"], sit["perfect_game"] = bool(flags.get("noHitter")), bool(flags.get("perfectGame"))
    box = ((live.get("boxscore") or {}).get("teams") or {})
    pid = sit.get("pitcher_id") or (((live.get("linescore") or {}).get("defense") or {}).get("pitcher") or {}).get("id")
    if pid:
        for side in ("home", "away"):
            stats = ((box.get(side) or {}).get("players") or {}).get(f"ID{pid}") or {}
            n = ((stats.get("stats") or {}).get("pitching") or {}).get("numberOfPitches")
            if n is not None:
                sit["pitch_count"] = _int(n)
                break
    decisions = dict(game.get("decisions") or {})
    for role in ("winner", "loser", "save"):
        who = (live.get("decisions") or {}).get(role) or {}
        if who.get("fullName"):
            line = who["fullName"]
            rec = _season_pitching(box, who.get("id"))
            if rec:
                line += f" ({rec['wins']}-{rec['losses']})" if role != "save" else f" ({rec['saves']})"
            decisions[role] = line
    return {**game, "situation": sit, "decisions": decisions}


def _season_pitching(box: dict[str, Any], pid: Any) -> dict[str, Any] | None:
    if not pid:
        return None
    for side in ("home", "away"):
        p = ((box.get(side) or {}).get("players") or {}).get(f"ID{pid}") or {}
        stats = (p.get("seasonStats") or {}).get("pitching")
        if stats:
            return {"wins": _int(stats.get("wins")), "losses": _int(stats.get("losses")), "saves": _int(stats.get("saves")), "era": stats.get("era", "")}
    return None


# -- standings ------------------------------------------------------------------------


def _pct(row: dict[str, Any]) -> float:
    try:
        return float(row.get("win_pct") or 0)
    except ValueError:
        return 0.0


def normalize_standings(payload: dict[str, Any]) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    for rec in payload.get("records") or []:
        for tr in rec.get("teamRecords") or []:
            t = tr.get("team") or {}
            abbrev = abbrev_for(t.get("id"), t.get("abbreviation"))
            if not abbrev:
                continue
            lr = tr.get("leagueRecord") or {}
            wins, losses = _int(tr.get("wins", lr.get("wins"))), _int(tr.get("losses", lr.get("losses")))
            splits = {s.get("type"): s for s in (tr.get("records") or {}).get("splitRecords") or []}
            l10 = splits.get("lastTen") or {}
            elim = str(tr.get("eliminationNumber") or "")
            rows[abbrev] = {
                "abbrev": abbrev, "conference": LEAGUE_OF.get(abbrev, ""), "division": DIVISION_OF.get(abbrev, ""),
                "gp": _int(tr.get("gamesPlayed"), wins + losses), "wins": wins, "losses": losses, "otl": 0,
                "points": wins,                                        # shared boards sort on it; the column shows GB
                "win_pct": tr.get("winningPercentage") or lr.get("pct") or "",
                "games_back": tr.get("gamesBack") or "-", "wildcard_games_back": tr.get("wildCardGamesBack") or "-",
                "streak": (tr.get("streak") or {}).get("streakCode", ""), "l10": [_int(l10.get("wins")), _int(l10.get("losses"))],
                "run_diff": _int(tr.get("runDifferential")),
                "clinch": "y" if tr.get("divisionChamp") else "x" if tr.get("clinched") else "", "eliminated": elim == "E",
                "division_rank": _int(tr.get("divisionRank")), "league_rank": _int(tr.get("leagueRank")),
                "conference_rank": _int(tr.get("leagueRank")), "wildcard_rank": _int(tr.get("wildCardRank")),
                "sport_rank": _int(tr.get("sportRank")),
            }
    by_div: dict[str, list[str]] = {}
    for div in DIVISION_ORDER:
        teams = [t for t in rows if rows[t]["division"] == div]
        ordered = sorted(teams, key=lambda t: (rows[t]["division_rank"] or 99, -_pct(rows[t]), -rows[t]["wins"]))
        for i, t in enumerate(ordered, 1):
            rows[t]["division_rank"] = i
        if ordered:
            by_div[div] = ordered
    wildcard: dict[str, dict[str, list[str]]] = {}
    for league in ("AL", "NL"):
        members = [t for t in rows if rows[t]["conference"] == league]
        if not members:
            continue
        leaders = sorted((t for t in members if rows[t]["division_rank"] == 1), key=lambda t: (-_pct(rows[t]), -rows[t]["wins"]))
        chasers = sorted((t for t in members if rows[t]["division_rank"] != 1),
                         key=lambda t: (rows[t]["wildcard_rank"] or 99, -_pct(rows[t]), -rows[t]["wins"]))
        for i, t in enumerate(chasers, 1):
            rows[t]["wildcard_rank"] = i
        for t in leaders:
            rows[t]["wildcard_rank"] = 0
        wildcard[league] = {"Leaders": leaders, "Wildcard": chasers}
    league = sorted(rows, key=lambda t: (rows[t]["sport_rank"] or 99, -_pct(rows[t]), -rows[t]["wins"]))
    for i, t in enumerate(league, 1):
        rows[t]["league_rank"] = i
    return {"teams": rows, "division": by_div, "wildcard": wildcard, "league": league}


def records_from_standings(standings: dict[str, Any] | None) -> dict[str, str]:
    return {a: f"{r['wins']}-{r['losses']}" for a, r in ((standings or {}).get("teams") or {}).items()}


# -- team summary ---------------------------------------------------------------------


def team_summary(abbrev: str, standings: dict[str, Any] | None, schedule: dict[str, Any] | None, today: str) -> dict[str, Any]:
    row = ((standings or {}).get("teams") or {}).get(abbrev) or {}
    games = normalize_schedule(schedule or {})
    prev = next_game = None
    for g in games:
        if g["phase"] == "postgame" and g["state"] == "FINAL":
            prev = g
        elif next_game is None and g["phase"] != "postgame" and g["date"] >= today:
            next_game = g
    return {
        "abbrev": abbrev, "sport": "mlb",
        "record": {"gp": row.get("gp", 0), "points": row.get("points", 0), "wins": row.get("wins", 0), "losses": row.get("losses", 0),
                   "otl": 0, "l10": row.get("l10", []), "streak": row.get("streak", ""), "division": row.get("division", ""),
                   "division_rank": row.get("division_rank", 0), "win_pct": row.get("win_pct", ""),
                   "games_back": row.get("games_back", "-"), "wildcard_rank": row.get("wildcard_rank", 0),
                   "wildcard_games_back": row.get("wildcard_games_back", "-")},
        "prev_game": _sched(prev, abbrev), "next_game": _sched(next_game, abbrev),
    }


def _sched(g: dict[str, Any] | None, abbrev: str) -> dict[str, Any] | None:
    if not g:
        return None
    is_home = g["home"]["abbrev"] == abbrev
    us, them = (g["home"], g["away"]) if is_home else (g["away"], g["home"])
    result = ""
    if g["phase"] == "postgame" and g["state"] == "FINAL":
        result = "W" if us["score"] > them["score"] else "L" if us["score"] < them["score"] else "T"
    return {"id": g["id"], "date": g["date"], "start_time_utc": g["start_time_utc"], "home": is_home, "opponent": them["abbrev"],
            "score": us["score"], "opponent_score": them["score"], "result": result, "state": g["state"],
            "probable_pitcher": us.get("probable_pitcher", "")}


# -- season -----------------------------------------------------------------------------


def season_info(seasons_payload: dict[str, Any], today: date, opener_schedule: dict[str, Any] | None = None,
                abbrev: str | None = None, standings_season: int | None = None) -> dict[str, Any]:
    """{phase, dates..., days_to_preseason/regular, first_game} from ``/seasons?season=YYYY``."""
    season = next(iter(seasons_payload.get("seasons") or []), {}) or {}

    def d(key: str) -> date | None:
        v = season.get(key)
        try:
            return date.fromisoformat(v) if v else None
        except ValueError:
            return None

    spring, reg, reg_end = d("springStartDate") or d("preSeasonStartDate"), d("regularSeasonStartDate"), d("regularSeasonEndDate")
    post, post_end = d("postSeasonStartDate"), d("postSeasonEndDate")
    if reg and reg_end and reg <= today <= reg_end:
        phase = "regular"
    elif post and post_end and post <= today <= post_end:
        phase = "playoffs"
    elif reg_end and post and reg_end < today < post:
        phase = "playoffs"                                              # the gap between the last game and the wild card round
    elif spring and reg and spring <= today < reg:
        phase = "preseason"
    else:
        phase = "offseason"
    season_id = _int(season.get("seasonId"), today.year)
    first_game = None
    if abbrev and opener_schedule:
        for g in normalize_schedule(opener_schedule):
            if g["game_type"] == "R":
                us_home = g["home"]["abbrev"] == abbrev
                them = g["away"] if us_home else g["home"]
                first_game = {"date": g["date"], "home": us_home, "opponent": them["abbrev"], "start_time_utc": g["start_time_utc"]}
                break
    return {
        "sport": "mlb", "phase": phase, "season_id": season_id,
        "preseason_start": spring.isoformat() if spring else None, "regular_start": reg.isoformat() if reg else None,
        "regular_end": reg_end.isoformat() if reg_end else None, "playoff_end": post_end.isoformat() if post_end else None,
        "days_to_preseason": (spring - today).days if spring else None, "days_to_regular": (reg - today).days if reg else None,
        "standings_season_id": standings_season,
        "standings_final": bool(standings_season and standings_season < season_id),
        "first_game": first_game,
    }
