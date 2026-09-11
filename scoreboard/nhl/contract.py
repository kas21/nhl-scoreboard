"""What the NHL feed has to keep providing — the fields ``normalize.py`` actually reads.

``api-web.nhle.com`` is undocumented and unversioned, and the normaliser is deliberately
forgiving: a renamed field does not raise, it quietly becomes a default. These checks turn
that into something visible. They run three ways from this one spec: against the recorded
fixtures in the test suite, against the live API on a schedule (``tests/test_nhl_contract.py``),
and at runtime in the source, which reports every note through ``SourceContext.drift``.

Every check returns a list of notes, empty when the payload matches. Notes are stable strings
without per-game values so the same problem collapses to one line on the diagnostics page.
"""
from __future__ import annotations

from typing import Any

GAME_FIELDS = ("id", "gameType", "gameState", "gameDate", "startTimeUTC", "awayTeam", "homeTeam")
# periodDescriptor and clock are absent from games that have not started — confirmed against
# the live feed in the off-season, where every game is FUT and carries neither. normalize_game
# already reads both through `or {}`, so this is the real contract, not a workaround.
STARTED_STATES = frozenset({"LIVE", "CRIT", "OVER", "FINAL", "OFF"})
TEAM_FIELDS = ("abbrev",)
TEAM_STARTED_FIELDS = ("abbrev", "score")     # no score on a game that has not been played yet
PERIOD_FIELDS = ("number", "periodType")
CLOCK_FIELDS = ("timeRemaining", "running", "inIntermission")
STANDINGS_FIELDS = ("teamAbbrev", "conferenceName", "divisionName", "gamesPlayed", "wins", "losses",
                    "otLosses", "points", "l10Wins", "l10Losses", "l10OtLosses", "streakCode",
                    "streakCount", "divisionSequence", "conferenceSequence", "leagueSequence",
                    "wildcardSequence")
GOAL_FIELDS = ("teamAbbrev", "timeInPeriod", "name", "firstName", "lastName", "goalsToDate",
               "strength", "assists", "awayScore", "homeScore")
PENALTY_FIELDS = ("teamAbbrev", "timeInPeriod", "type", "duration", "descKey")

# Values normalize maps through a lookup rather than passing along: an unknown one is not a
# missing field, it is a state the director cannot classify, so it must not appear quietly.
KNOWN_GAME_STATES = frozenset({"FUT", "PRE", "LIVE", "CRIT", "OVER", "FINAL", "OFF"})
KNOWN_PERIOD_TYPES = frozenset({"REG", "OT", "SO"})
KNOWN_SCHEDULE_STATES = frozenset({"OK", "PPD", "SUSP", "CNCL"})
SITUATION_CODE_LENGTH = 4


def _missing(obj: Any, fields: tuple[str, ...], what: str) -> list[str]:
    if not isinstance(obj, dict):
        return [f"{what} is not an object"]
    return [f"{what} missing {f}" for f in fields if f not in obj]


def check_game(game: dict[str, Any]) -> list[str]:
    """A ``/score`` (or schedule) game still carries what ``normalize_game`` reads."""
    notes = _missing(game, GAME_FIELDS, "score game")
    state = game.get("gameState")
    if state is not None and state not in KNOWN_GAME_STATES:
        notes.append(f"unknown gameState {state!r}")
    sched = game.get("gameScheduleState")
    if sched is not None and sched not in KNOWN_SCHEDULE_STATES:
        notes.append(f"unknown gameScheduleState {sched!r}")
    started = state in STARTED_STATES
    for side in ("awayTeam", "homeTeam"):
        if side in game:
            notes += _missing(game[side], TEAM_STARTED_FIELDS if started else TEAM_FIELDS, side)
    descriptor = game.get("periodDescriptor") or {}
    if started and not descriptor:
        notes.append("started game has no periodDescriptor — the period label goes blank")
    if descriptor:
        notes += _missing(descriptor, PERIOD_FIELDS, "periodDescriptor")
        ptype = descriptor.get("periodType") if isinstance(descriptor, dict) else None
        if ptype is not None and ptype not in KNOWN_PERIOD_TYPES:
            notes.append(f"unknown periodType {ptype!r}")
    if state in ("LIVE", "CRIT"):
        notes += _missing(game.get("clock") or {}, CLOCK_FIELDS, "live game clock")
    return notes


def check_score_payload(payload: dict[str, Any]) -> list[str]:
    if "games" not in payload:
        return ["score payload has no 'games' key"]
    return _dedupe(note for game in payload["games"] or [] for note in check_game(game))


def check_landing(landing: dict[str, Any]) -> list[str]:
    """The landing feed is where the power play, goals and penalties come from."""
    notes = _missing(landing, ("id", "gameState", "awayTeam", "homeTeam"), "landing")
    situation = landing.get("situation")
    if situation:                            # only present while a game is actually being played
        code = situation.get("situationCode") if isinstance(situation, dict) else None
        if code is None:
            notes.append("situation lost situationCode — power play and pulled goalie go silently wrong")
        elif not (isinstance(code, str) and len(code) == SITUATION_CODE_LENGTH and code.isdigit()):
            notes.append(f"situationCode {code!r} is no longer {SITUATION_CODE_LENGTH} digits")
    summary = landing.get("summary") or {}
    for period in summary.get("scoring") or []:
        for goal in period.get("goals") or []:
            notes += _missing(goal, GOAL_FIELDS, "goal")
    for period in summary.get("penalties") or []:
        for pen in period.get("penalties") or []:
            notes += _missing(pen, PENALTY_FIELDS, "penalty")
    return _dedupe(notes)


def check_standings_payload(payload: dict[str, Any]) -> list[str]:
    if not payload.get("standings"):
        return ["standings payload has no rows"]
    return _dedupe(note for row in payload["standings"] for note in _missing(row, STANDINGS_FIELDS, "standings row"))


def _dedupe(notes: Any) -> list[str]:
    return list(dict.fromkeys(notes))
