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
        self.game_day_rollover_hour: int = 0        # sports.game_day_rollover_hour, set by the app (0 here keeps a bare context on the calendar day)
        self.log = logging.getLogger(f"source.{key}")
        self._drift_seen: set[str] = set()
        self._wake = asyncio.Event()                # set when this source's settings change: cut the nap short

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

        The nap ends early when the source's settings change (:meth:`wake`), so a new favourite
        or interval takes effect on the next poll rather than after the old interval runs out.
        ``until_poll`` is for a source that naps in pieces between fetches (to retire an alert
        on time, say): the diagnostics page then still shows the fetch, not the nap."""
        if self.health is not None:
            self.health.set_next_poll(self.key, self.health.now() + (seconds if until_poll is None else until_poll))
        try:
            self._wake.clear()
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except TimeoutError:
            pass
        finally:
            if self.health is not None:
                self.health.set_next_poll(self.key, None)

    def wake(self) -> None:
        """Cut the current nap short. Thread-safe: the config store calls this from the web thread."""
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._wake.set)
        else:
            self._wake.set()

    _loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def snapshot(self):
        return self._store.get()


@runtime_checkable
class DataSource(Protocol):
    key: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]

    async def run(self, ctx: SourceContext) -> None: ...


def source_enabled(ctx: SourceContext) -> bool:
    """A source with an ``enabled`` setting honours it; one without is always on."""
    try:
        return bool(getattr(ctx.config, "enabled", True))
    except Exception:
        return True


class SourceSupervisor:
    """Starts, stops and restarts the sources as the config says.

    Each source runs as a task while its ``enabled`` setting is on. Switching one off cancels
    its task and retracts everything it published (so its boards leave the rotation and the
    arbiter forgets its game at once); switching it back on starts it fresh. Any other change
    to a source's settings wakes it from its nap so the next poll uses the new values.
    ``reconcile`` is the one entry point; the app calls it at startup and on every config save.
    """

    def __init__(self, sources: dict[str, DataSource], contexts: dict[str, SourceContext], store: Any,
                 health: Any = None) -> None:
        self._sources = sources
        self._contexts = contexts
        self._store = store
        self._health = health
        self._tasks: dict[str, asyncio.Task] = {}
        self._seen: dict[str, Any] = {}             # key -> the raw settings the source last saw

    @property
    def running(self) -> set[str]:
        return {k for k, t in self._tasks.items() if not t.done()}

    async def reconcile(self, raw_settings: dict[str, Any] | None = None) -> None:
        """Bring the tasks in line with the config. ``raw_settings`` is ``config.sources`` (the
        dicts as saved), used to tell a real change from a save that touched something else."""
        for key, source in self._sources.items():
            ctx = self._contexts[key]
            want = source_enabled(ctx)
            have = key in self.running
            if want and not have:
                await self._start(key, source, ctx)
            elif have and not want:
                await self._stop(key)
            elif not want and key not in self._tasks:          # off from the start: say so on the diagnostics page
                self._tasks.pop(key, None)
                if self._health is not None:
                    self._health.set_disabled(key, True)
            elif have and raw_settings is not None and raw_settings.get(key) != self._seen.get(key):
                ctx.wake()
            if raw_settings is not None:
                self._seen[key] = raw_settings.get(key)

    async def _start(self, key: str, source: DataSource, ctx: SourceContext) -> None:
        if self._health is not None:
            self._health.set_disabled(key, False)
        self._tasks[key] = asyncio.create_task(run_source_forever(source, ctx), name=f"source:{key}")
        log.info("source %s enabled", key)

    async def _stop(self, key: str) -> None:
        task = self._tasks.pop(key, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        retracted = self._store.retract(key)
        if self._health is not None:
            self._health.set_disabled(key, True)
        log.info("source %s disabled; retracted %s", key, ", ".join(retracted) or "nothing")

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()


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
