"""Data source contract and runner."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, ClassVar, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel

from .health import DRIFT_NOTES_LIMIT, SourceHealth, TrackedHttp
from .store import SnapshotStore

log = logging.getLogger(__name__)

RESTART_BACKOFF_SECONDS = (2, 5, 15, 30, 60)


class SourceContext:
    """Everything a source is allowed to touch."""

    def __init__(
        self,
        key: str,
        store: SnapshotStore,
        config_getter: Callable[[], BaseModel],
        http: httpx.AsyncClient,
        health: SourceHealth | None = None,
    ) -> None:
        self.key = key
        self._store = store
        self._config_getter = config_getter
        self.health = health
        if health is not None:
            health.register(key)
        # Requests made through ctx.http are attributed to this source on the diagnostics page.
        self.http: httpx.AsyncClient = TrackedHttp(http, health, key) if health is not None else http  # type: ignore[assignment]
        self.timezone: str | None = None            # IANA name, set by the app from location config
        self.location: tuple[float, float] | None = None   # (lat, lon) from location config, if set
        self.log = logging.getLogger(f"source.{key}")
        self._drift_seen: set[str] = set()

    @property
    def config(self) -> BaseModel:
        """Live config; re-read it each loop so UI edits apply."""
        return self._config_getter()

    def publish(self, value: Any, subkey: str | None = None) -> None:
        key = f"{self.key}.{subkey}" if subkey else self.key
        self.publish_to(key, value)

    def publish_to(self, key: str, value: Any) -> None:
        """Publish under an arbitrary key (e.g. the sport-agnostic ``main_event``)."""
        self._store.publish(key, value, owner=self.key)
        if self.health is not None:
            self.health.record_publish(self.key, key)

    def drift(self, note: str) -> None:
        """Report that the feed no longer looks the way this source expects — an unknown enum
        value, a missing field, a team the registry has never heard of. Logged once per distinct
        note and counted on the diagnostics page; the source carries on with its best guess,
        so this is the signal that the guess may be wrong. Keep notes free of per-game values
        (ids, clocks) so the same problem collapses to one line."""
        if self.health is not None:
            new = self.health.record_drift(self.key, note)
        else:                                       # no registry (tests, one-off scripts): dedupe locally
            new = note not in self._drift_seen
            if new and len(self._drift_seen) < DRIFT_NOTES_LIMIT:
                self._drift_seen.add(note)
        if new:
            self.log.warning("feed drift: %s", note)

    async def sleep(self, seconds: float, *, until_poll: float | None = None) -> None:
        """Pause between polls; records when this source will next fetch so the UI can show it.

        ``until_poll`` is for a source that naps in pieces between fetches (to retire an alert
        on time, say): the diagnostics page then still shows the fetch, not the nap."""
        if self.health is not None:
            self.health.set_next_poll(self.key, self.health.now() + (seconds if until_poll is None else until_poll))
        try:
            await asyncio.sleep(seconds)
        finally:
            if self.health is not None:
                self.health.set_next_poll(self.key, None)

    def snapshot(self):
        return self._store.get()


@runtime_checkable
class DataSource(Protocol):
    key: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]

    async def run(self, ctx: SourceContext) -> None: ...


async def run_source_forever(source: DataSource, ctx: SourceContext) -> None:
    """Run a source, restarting with backoff if it crashes."""
    failures = 0
    health = ctx.health
    while True:
        try:
            if health is not None:
                health.set_running(source.key, True)
            await source.run(ctx)
            log.warning("source %s exited; restarting", source.key)
            if health is not None:
                health.record_crash(source.key, "run() returned")
            failures = 0
        except asyncio.CancelledError:
            if health is not None:
                health.set_running(source.key, False)
            raise
        except Exception as exc:
            delay = RESTART_BACKOFF_SECONDS[min(failures, len(RESTART_BACKOFF_SECONDS) - 1)]
            failures += 1
            log.exception("source %s crashed; restarting in %ss", source.key, delay)
            if health is not None:
                health.record_crash(source.key, f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(delay)
