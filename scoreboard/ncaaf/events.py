"""College football event detectors: the NFL scoring rule on ``ncaaf.main_event``."""
from __future__ import annotations

from collections.abc import Iterable

from ..data import Event, Snapshot
from ..nfl.events import detect_scoring


def detect_ncaaf(prev: Snapshot, new: Snapshot) -> Iterable[Event]:
    return detect_scoring(prev, new, "ncaaf")
