"""Events derived by diffing consecutive snapshots.

Sport packages register *detectors*: pure functions ``(prev, next) -> events``.
The director consumes events to interrupt the playlist (goal animation, etc).
"""
from __future__ import annotations

import threading
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
    """Runs registered detectors on every snapshot change and queues results.

    The queue is handed between threads: sources fill it from the asyncio loop (via
    ``SnapshotStore.publish``) and the director empties it from the render thread. The
    lock is what makes that handover lossless — without it, a detector appending between
    ``drain()`` reading the queue and clearing it has its event silently discarded, and
    the event most likely to be in flight is the goal that just happened.

    Detectors run outside the lock: they are pure diffs of two snapshots, but they are
    also third-party plugin code, and holding a lock across a call we do not control
    would let one slow detector stall the render thread.
    """

    def __init__(self) -> None:
        self._detectors: list[Detector] = []
        self._queue: list[Event] = []
        self._lock = threading.Lock()

    def register(self, detector: Detector) -> None:
        with self._lock:
            self._detectors.append(detector)

    def on_snapshot(self, prev: Snapshot, new: Snapshot) -> None:
        with self._lock:
            detectors = list(self._detectors)
        found = [event for detector in detectors for event in detector(prev, new)]
        if found:
            with self._lock:
                self._queue.extend(found)

    def drain(self) -> tuple[Event, ...]:
        """Return queued events, collapsed so a burst (missed polls, restart mid-game)
        plays at most one event per (kind, team) — the latest — instead of a backlog."""
        with self._lock:
            queued, self._queue = self._queue, []
        latest: dict[tuple[str, str | None], Event] = {}
        for ev in queued:
            latest[(ev.kind, ev.team)] = ev
        return tuple(latest.values())
