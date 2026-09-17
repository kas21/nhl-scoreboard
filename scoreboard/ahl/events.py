"""AHL event detectors: the NHL goal and penalty rule on ``ahl.main_event``."""
from __future__ import annotations

from collections.abc import Iterable

from ..data import Event, Snapshot
from ..nhl.events import detect_goals


def detect_ahl(prev: Snapshot, new: Snapshot) -> Iterable[Event]:
    return detect_goals(prev, new, "ahl")
