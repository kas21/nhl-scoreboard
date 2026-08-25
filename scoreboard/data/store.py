"""Immutable, versioned application data snapshot.

Data sources publish JSON-shaped sub-trees under a key; the store swaps in a
brand-new ``Snapshot`` each time. Readers (the render thread) never lock.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

Listener = Callable[["Snapshot", "Snapshot"], None]


@dataclass(frozen=True)
class Snapshot:
    version: int = 0
    data: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    updated: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def has(self, *keys: str) -> bool:
        return all(k in self.data for k in keys)

    def age(self, key: str, now: float | None = None) -> float | None:
        """Seconds since ``key`` was last published, or None if never."""
        ts = self.updated.get(key)
        if ts is None:
            return None
        return (now if now is not None else time.time()) - ts

    def with_value(self, key: str, value: Any, ts: float | None = None) -> Snapshot:
        data = dict(self.data)
        data[key] = value
        updated = dict(self.updated)
        updated[key] = ts if ts is not None else time.time()
        return Snapshot(self.version + 1, MappingProxyType(data), MappingProxyType(updated))


class SnapshotStore:
    def __init__(self) -> None:
        self._snapshot = Snapshot()
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []

    def get(self) -> Snapshot:
        return self._snapshot

    def subscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def publish(self, key: str, value: Any) -> Snapshot:
        with self._lock:
            prev = self._snapshot
            new = prev.with_value(key, value)
            self._snapshot = new
            listeners = list(self._listeners)
        for listener in listeners:
            listener(prev, new)
        return new
