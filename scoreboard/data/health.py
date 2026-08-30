"""Per-source health: what each background fetcher is doing, for the web UI.

Fed by three hooks every source already passes through — the tracked ``ctx.http``
client, ``ctx.publish()`` and the ``run_source_forever`` supervisor — so plugins get
monitoring for free. All updates replace an immutable ``SourceStats``; readers never lock.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

OFFLINE_AFTER_FAILURES = 3      # consecutive fetch failures before a source counts as offline
ERROR_TEXT_LIMIT = 200


@dataclass(frozen=True)
class SourceStats:
    key: str
    running: bool = False
    started_at: float | None = None
    restarts: int = 0
    fetches: int = 0
    failures: int = 0
    error_streak: int = 0
    last_fetch_at: float | None = None
    last_ok_at: float | None = None
    last_latency_ms: float | None = None
    last_url: str | None = None
    last_error: str | None = None
    last_error_at: float | None = None
    next_poll_at: float | None = None
    publishes: int = 0
    last_publish_at: float | None = None
    keys: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        if not self.running and self.restarts:
            return "crashed"
        if self.error_streak >= OFFLINE_AFTER_FAILURES:
            return "offline"
        if self.error_streak > 0:
            return "degraded"
        if self.fetches == 0 and self.publishes == 0:
            return "starting"
        return "ok"

    def to_dict(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now

        def ago(ts: float | None) -> float | None:
            return None if ts is None else round(now - ts, 1)

        return {
            "key": self.key,
            "status": self.status,
            "running": self.running,
            "uptime": ago(self.started_at) if self.running else None,
            "restarts": self.restarts,
            "fetches": self.fetches,
            "failures": self.failures,
            "error_streak": self.error_streak,
            "last_fetch_ago": ago(self.last_fetch_at),
            "last_ok_ago": ago(self.last_ok_at),
            "last_latency_ms": self.last_latency_ms,
            "last_url": self.last_url,
            "last_error": self.last_error,
            "last_error_ago": ago(self.last_error_at),
            "next_poll_in": None if self.next_poll_at is None else round(self.next_poll_at - now, 1),
            "publishes": self.publishes,
            "last_publish_ago": ago(self.last_publish_at),
            "keys": list(self.keys),
        }


class SourceHealth:
    """Thread-safe registry of ``SourceStats``; every mutation swaps in a new frozen record."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._stats: dict[str, SourceStats] = {}

    def now(self) -> float:
        return self._clock()

    def register(self, key: str) -> None:
        with self._lock:
            self._stats.setdefault(key, SourceStats(key=key))

    def get(self, key: str) -> SourceStats | None:
        return self._stats.get(key)

    def all(self) -> dict[str, SourceStats]:
        return dict(self._stats)

    def to_list(self, now: float | None = None) -> list[dict[str, Any]]:
        now = self._clock() if now is None else now
        return [s.to_dict(now) for _, s in sorted(self._stats.items())]

    def _update(self, key: str, **changes: Any) -> None:
        with self._lock:
            current = self._stats.get(key)
            if current is None:
                return                      # never registered: nothing to attribute this to
            self._stats[key] = replace(current, **changes)

    def set_running(self, key: str, running: bool) -> None:
        now = self._clock()
        self._update(key, running=running, started_at=now if running else None)

    def record_crash(self, key: str, error: str) -> None:
        current = self._stats.get(key)
        if current is None:
            return
        now = self._clock()
        self._update(key, running=False, started_at=None, restarts=current.restarts + 1,
                     last_error=_truncate(error), last_error_at=now)

    def record_fetch(self, key: str, *, ok: bool, latency_ms: float, url: str | None = None,
                     error: str | None = None) -> None:
        current = self._stats.get(key)
        if current is None:
            return
        now = self._clock()
        changes: dict[str, Any] = {
            "fetches": current.fetches + 1,
            "last_fetch_at": now,
            "last_latency_ms": round(latency_ms, 1),
            "last_url": url if url is not None else current.last_url,
        }
        if ok:
            changes.update(error_streak=0, last_ok_at=now)
        else:
            changes.update(failures=current.failures + 1, error_streak=current.error_streak + 1,
                           last_error=_truncate(error or "request failed"), last_error_at=now)
        self._update(key, **changes)

    def record_publish(self, key: str, snapshot_key: str) -> None:
        current = self._stats.get(key)
        if current is None:
            return
        keys = current.keys if snapshot_key in current.keys else (*current.keys, snapshot_key)
        self._update(key, publishes=current.publishes + 1, last_publish_at=self._clock(), keys=keys)

    def set_next_poll(self, key: str, at: float | None) -> None:
        self._update(key, next_poll_at=at)


class TrackedHttp:
    """Wraps a shared ``httpx.AsyncClient`` so one source's requests are attributed to it."""

    def __init__(self, client: Any, health: SourceHealth, key: str, clock: Callable[[], float] = time.monotonic) -> None:
        self._client = client
        self._health = health
        self._key = key
        self._clock = clock

    async def request(self, method: str, url: Any, **kwargs: Any) -> Any:
        started = self._clock()
        try:
            response = await self._client.request(method, url, **kwargs)
        except Exception as exc:
            self._health.record_fetch(self._key, ok=False, latency_ms=_ms(self._clock() - started),
                                      url=str(url), error=f"{type(exc).__name__}: {exc}")
            raise
        error = None if not response.is_error else f"HTTP {response.status_code} for {response.url}"
        self._health.record_fetch(self._key, ok=error is None, latency_ms=_ms(self._clock() - started),
                                  url=str(url), error=error)
        return response

    async def get(self, url: Any, **kwargs: Any) -> Any:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: Any, **kwargs: Any) -> Any:
        return await self.request("POST", url, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def _ms(seconds: float) -> float:
    return seconds * 1000.0


def _truncate(text: str) -> str:
    return text if len(text) <= ERROR_TEXT_LIMIT else text[: ERROR_TEXT_LIMIT - 1] + "…"
