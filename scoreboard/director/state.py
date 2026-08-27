"""Application state derived from the snapshot.

Sport sources publish a normalised ``main_event`` dict with a ``phase`` field so
the director stays sport-agnostic::

    {"phase": "pregame"|"live"|"intermission"|"postgame", ...}
"""
from __future__ import annotations

from enum import Enum

from ..data import Snapshot

MAIN_EVENT_KEY = "main_event"
SYSTEM_KEY = "system"


class AppState(str, Enum):
    BOOT = "boot"
    ERROR = "error"
    OFFSEASON = "offseason"
    OFFDAY = "offday"
    PREGAME = "pregame"
    LIVE = "live"
    INTERMISSION = "intermission"
    POSTGAME = "postgame"


PLAYLIST_STATES = (AppState.OFFSEASON, AppState.OFFDAY, AppState.PREGAME, AppState.LIVE, AppState.INTERMISSION, AppState.POSTGAME)
_PHASES = {s.value: s for s in (AppState.PREGAME, AppState.LIVE, AppState.INTERMISSION, AppState.POSTGAME)}


def is_offline(snapshot: Snapshot) -> bool:
    return (snapshot.get(SYSTEM_KEY) or {}).get("online") is False


def has_data(snapshot: Snapshot) -> bool:
    """Anything besides the system key has ever been published."""
    return any(k != SYSTEM_KEY for k in snapshot.data)


def compute_state(snapshot: Snapshot) -> AppState:
    if is_offline(snapshot) and not has_data(snapshot):
        return AppState.ERROR                      # nothing to show: clock until we get data
    event = snapshot.get(MAIN_EVENT_KEY)
    if not event:
        return AppState.OFFSEASON if is_offseason(snapshot) else AppState.OFFDAY
    return _PHASES.get(event.get("phase"), AppState.OFFDAY)


def is_offseason(snapshot: Snapshot) -> bool:
    """Every sport that has reported a season phase says off-season (and at least one has)."""
    phases = [v.get("phase") for k, v in snapshot.data.items() if k.endswith(".season") and isinstance(v, dict)]
    return bool(phases) and all(p == "offseason" for p in phases)
