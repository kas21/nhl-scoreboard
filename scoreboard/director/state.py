"""Application state derived from the snapshot.

Sport sources publish a normalised ``main_event`` dict with a ``phase`` field so
the director stays sport-agnostic::

    {"phase": "pregame"|"live"|"intermission"|"postgame", "start_time_utc": ..., ...}

A ``pregame`` phase is split in two by the clock: GAMEDAY from midnight until
``sports.pregame_hours`` before the start, PREGAME from there to the first drop.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum

from ..data import Snapshot
from ..isotime import parse_iso

MAIN_EVENT_KEY = "main_event"
SYSTEM_KEY = "system"


class AppState(str, Enum):
    BOOT = "boot"
    ERROR = "error"
    OFFSEASON = "offseason"
    OFFDAY = "offday"
    GAMEDAY = "gameday"
    PREGAME = "pregame"
    LIVE = "live"
    INTERMISSION = "intermission"
    POSTGAME = "postgame"


PLAYLIST_STATES = (AppState.OFFSEASON, AppState.OFFDAY, AppState.GAMEDAY, AppState.PREGAME, AppState.LIVE,
                   AppState.INTERMISSION, AppState.POSTGAME)
_PHASES = {s.value: s for s in (AppState.PREGAME, AppState.LIVE, AppState.INTERMISSION, AppState.POSTGAME)}
DEFAULT_PREGAME_HOURS = 1.0


def is_offline(snapshot: Snapshot) -> bool:
    return (snapshot.get(SYSTEM_KEY) or {}).get("online") is False


def has_data(snapshot: Snapshot) -> bool:
    """Anything besides the system key has ever been published."""
    return any(k != SYSTEM_KEY for k in snapshot.data)


def compute_state(snapshot: Snapshot, now: datetime | None = None, pregame_hours: float = DEFAULT_PREGAME_HOURS) -> AppState:
    """The state for ``snapshot`` at ``now`` (aware; defaults to the clock).

    ``pregame_hours`` is how long before the start the PREGAME playlist takes over from
    GAMEDAY. A game whose start time is missing or unreadable is PREGAME all day, as it
    was before the split: better the matchup board than a day of standings.
    """
    if is_offline(snapshot) and not has_data(snapshot):
        return AppState.ERROR                      # nothing to show: clock until we get data
    event = snapshot.get(MAIN_EVENT_KEY)
    if not event:
        return AppState.OFFSEASON if is_offseason(snapshot) else AppState.OFFDAY
    state = _PHASES.get(event.get("phase"), AppState.OFFDAY)
    if state == AppState.PREGAME and not pregame_window_open(event, now, pregame_hours):
        return AppState.GAMEDAY
    return state


def pregame_window_open(event: dict, now: datetime | None = None, pregame_hours: float = DEFAULT_PREGAME_HOURS) -> bool:
    """True once the start is ``pregame_hours`` away or less (or unknown)."""
    start = parse_iso(str(event.get("start_time_utc") or ""))
    if start is None:
        return True
    now = now or datetime.now(UTC)
    return (start - now).total_seconds() <= pregame_hours * 3600


LONG_OVER_DAYS = 10**6


def season_over_days(season: dict | None, today: date) -> int | None:
    """Days since a sport's season finished, from its ``<sport>.season`` record: None while
    it is in season (or has said nothing), else the count from ``ended``.

    A feed only says when the season ended for a while: the NHL's dates roll over to next
    season a few weeks into the summer, MLB's with the new year. An off-season with no
    ``ended`` is therefore one that is long over, and reads as a very large number.
    """
    if not isinstance(season, dict) or season.get("phase") != "offseason":
        return None
    try:
        return max(0, (today - date.fromisoformat(str(season.get("ended") or ""))).days)
    except ValueError:
        return LONG_OVER_DAYS


def dormant_sports(snapshot: Snapshot, today: date, grace_days: int | None) -> frozenset[str]:
    """The sports whose season has been over for more than ``grace_days`` (None = never):
    what last season left behind — the record, the final standings, the last game's recap —
    is stale there. A sport with a game of its own on today is never one, whatever its season
    record says: the Simulator's game in July, or a feed that is late to notice opening day.
    """
    if grace_days is None:
        return frozenset()
    out = set()
    for key, season in snapshot.data.items():
        if not key.endswith(".season"):
            continue
        sport = key[: -len(".season")]
        days = season_over_days(season, today)
        if days is not None and days > grace_days and not snapshot.get(f"{sport}.main_event"):
            out.add(sport)
    return frozenset(out)


def is_offseason(snapshot: Snapshot) -> bool:
    """Every sport that has reported a season phase says off-season (and at least one has)."""
    phases = [v.get("phase") for k, v in snapshot.data.items() if k.endswith(".season") and isinstance(v, dict)]
    return bool(phases) and all(p == "offseason" for p in phases)
