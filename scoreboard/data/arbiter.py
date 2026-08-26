"""Picks the app-wide ``main_event`` from every sport's ``<sport>.main_event``.

Sports publish their own candidate (or None). The arbiter republishes the winner
under ``main_event``: any live game first (by sport priority), else the highest
priority sport that has a game today.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .store import Snapshot, SnapshotStore

MAIN_EVENT = "main_event"
SUFFIX = ".main_event"
ACTIVE = ("live", "intermission")


def choose(candidates: dict[str, dict[str, Any] | None], priority: list[str]) -> dict[str, Any] | None:
    order = [s for s in priority if s in candidates] + sorted(s for s in candidates if s not in priority)
    live = [candidates[s] for s in order if candidates[s] and candidates[s].get("phase") in ACTIVE]
    if live:
        return live[0]
    for s in order:
        if candidates[s]:
            return candidates[s]
    return None


class MainEventArbiter:
    def __init__(self, store: SnapshotStore, priority_getter: Callable[[], list[str]]) -> None:
        self._store = store
        self._priority = priority_getter
        store.subscribe(self.on_snapshot)

    def on_snapshot(self, prev: Snapshot, new: Snapshot) -> None:
        changed = [k for k in new.data if k.endswith(SUFFIX) and new.updated.get(k) != prev.updated.get(k)]
        if not changed:
            return
        candidates = {k[: -len(SUFFIX)]: new.get(k) for k in new.data if k.endswith(SUFFIX)}
        winner = choose(candidates, list(self._priority()))
        if winner != new.get(MAIN_EVENT) or MAIN_EVENT not in new.data:
            self._store.publish(MAIN_EVENT, winner)
