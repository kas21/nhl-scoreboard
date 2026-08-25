"""Events derived by diffing consecutive snapshots.

Sport packages register *detectors*: pure functions ``(prev, next) -> events``.
The director consumes events to interrupt the playlist (goal animation, etc).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .store import Snapshot


@dataclass(frozen=True)
class Event:
    kind: str                      # e.g. "nhl.goal", "nhl.penalty", "state_change"
    team: str | None = None        # team abbrev when relevant
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0


Detector = Callable[[Snapshot, Snapshot], Iterable[Event]]


class EventBus:
    """Runs registered detectors on every snapshot change and queues results."""

    def __init__(self) -> None:
        self._detectors: list[Detector] = []
        self._queue: list[Event] = []

    def register(self, detector: Detector) -> None:
        self._detectors.append(detector)

    def on_snapshot(self, prev: Snapshot, new: Snapshot) -> None:
        for detector in self._detectors:
            self._queue.extend(detector(prev, new))

    def drain(self) -> tuple[Event, ...]:
        events, self._queue = tuple(self._queue), []
        return events
