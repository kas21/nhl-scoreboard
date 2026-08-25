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
    OFFDAY = "offday"
    PREGAME = "pregame"
    LIVE = "live"
    INTERMISSION = "intermission"
    POSTGAME = "postgame"


PLAYLIST_STATES = (AppState.OFFDAY, AppState.PREGAME, AppState.LIVE, AppState.INTERMISSION, AppState.POSTGAME)
_PHASES = {s.value: s for s in (AppState.PREGAME, AppState.LIVE, AppState.INTERMISSION, AppState.POSTGAME)}


def compute_state(snapshot: Snapshot) -> AppState:
    system = snapshot.get(SYSTEM_KEY) or {}
    if system.get("online") is False:
        return AppState.ERROR
    event = snapshot.get(MAIN_EVENT_KEY)
    if not event:
        return AppState.OFFDAY
    return _PHASES.get(event.get("phase"), AppState.OFFDAY)
