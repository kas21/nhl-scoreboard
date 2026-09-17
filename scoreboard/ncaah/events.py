"""College hockey event detectors: the NHL goal rule on ``ncaah.main_event``.

ESPN's hockey scoreboard has no penalty or situation feed, so only goals and state changes
ever fire here; the shared detector's penalty and power-play branches simply see no data.
"""
from __future__ import annotations

from collections.abc import Iterable

from ..data import Event, Snapshot
from ..nhl.events import detect_goals


def detect_ncaah(prev: Snapshot, new: Snapshot) -> Iterable[Event]:
    return detect_goals(prev, new, "ncaah")
