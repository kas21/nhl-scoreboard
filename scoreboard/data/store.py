"""Immutable, versioned application data snapshot.

Data sources publish JSON-shaped sub-trees under a key; the store swaps in a
brand-new ``Snapshot`` each time. Readers (the render thread) never lock.

A key can be *claimed*: while an owner holds a claim on it, publishes from anyone else
are shadowed (kept, not applied) and the last shadowed value — or the value that was
there before the claim — is put back when the claim is released. That is how the
simulator takes a feed over without stopping the source behind it: the NHL source keeps
polling, its data keeps arriving, and the moment the simulation stops the panel is back
on real data without waiting for the next poll.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

log = logging.getLogger(__name__)

Listener = Callable[["Snapshot", "Snapshot"], None]

_MISSING = object()


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


class ClaimError(Exception):
    """The key is already claimed by a different owner."""


class SnapshotStore:
    def __init__(self) -> None:
        self._snapshot = Snapshot()
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._claims: dict[str, str] = {}           # key -> owner holding it
        self._shadow: dict[str, Any] = {}           # key -> last value published by a non-owner while claimed
        self._restore: dict[str, Any] = {}          # key -> value before the claim (_MISSING if absent)

    def get(self) -> Snapshot:
        return self._snapshot

    def subscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def publish(self, key: str, value: Any, owner: str | None = None) -> Snapshot:
        """Replace ``key``; ``owner`` names who is publishing so a claim can turn it away.

        A publish to a key someone else has claimed is shadowed: nothing changes and no
        listener runs, but the value is kept for when the claim is released."""
        with self._lock:
            holder = self._claims.get(key)
            if holder is not None and holder != owner:
                self._shadow[key] = value
                return self._snapshot
            prev, new, listeners = self._swap(key, value)
        _notify(listeners, prev, new)
        return new

    def _swap(self, key: str, value: Any) -> tuple[Snapshot, Snapshot, list[Listener]]:
        """Swap the snapshot. Called with the lock held; the caller runs the listeners
        after letting go of it, so a slow listener never stalls a publish elsewhere."""
        prev = self._snapshot
        new = prev.with_value(key, value)
        self._snapshot = new
        return prev, new, list(self._listeners)

    # -- claims ---------------------------------------------------------------

    def claim(self, keys: Any, owner: str) -> None:
        """Take ``keys`` over for ``owner``: only its publishes land until ``release``.

        Re-claiming a key you already hold is a no-op; a key held by someone else raises
        ``ClaimError`` and nothing is claimed, so a partial takeover cannot happen."""
        keys = [keys] if isinstance(keys, str) else list(keys)
        with self._lock:
            for key in keys:
                holder = self._claims.get(key)
                if holder is not None and holder != owner:
                    raise ClaimError(f"{key!r} is already claimed by {holder!r}")
            for key in keys:
                if key in self._claims:
                    continue
                self._claims[key] = owner
                self._restore[key] = self._snapshot.data.get(key, _MISSING)
                self._shadow.pop(key, None)

    def release(self, keys: Any, owner: str) -> None:
        """Give ``keys`` back. What was shadowed while claimed — else what was there before
        the claim — is published again so readers see real data straight away. Keys the
        owner does not hold are ignored."""
        keys = [keys] if isinstance(keys, str) else list(keys)
        swaps: list[tuple[Snapshot, Snapshot, list[Listener]]] = []
        with self._lock:
            for key in keys:
                if self._claims.get(key) != owner:
                    continue
                del self._claims[key]
                restore = self._restore.pop(key, _MISSING)
                if key in self._shadow:
                    swaps.append(self._swap(key, self._shadow.pop(key)))
                elif restore is not _MISSING:
                    swaps.append(self._swap(key, restore))
                else:
                    log.debug("released %s with nothing to restore; keeping the last value", key)
        for prev, new, listeners in swaps:
            _notify(listeners, prev, new)

    def claims(self) -> dict[str, str]:
        """Which keys are claimed, and by whom."""
        with self._lock:
            return dict(self._claims)

    def claimed_by(self, key: str) -> str | None:
        return self._claims.get(key)


def _notify(listeners: list[Listener], prev: Snapshot, new: Snapshot) -> None:
    for listener in listeners:
        listener(prev, new)
