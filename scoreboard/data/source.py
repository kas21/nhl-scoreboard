"""Data source contract and runner."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, ClassVar, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel

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
    ) -> None:
        self.key = key
        self._store = store
        self._config_getter = config_getter
        self.http = http
        self.timezone: str | None = None            # IANA name, set by the app from location config
        self.log = logging.getLogger(f"source.{key}")

    @property
    def config(self) -> BaseModel:
        """Live config; re-read it each loop so UI edits apply."""
        return self._config_getter()

    def publish(self, value: Any, subkey: str | None = None) -> None:
        key = f"{self.key}.{subkey}" if subkey else self.key
        self._store.publish(key, value)

    def publish_to(self, key: str, value: Any) -> None:
        """Publish under an arbitrary key (e.g. the sport-agnostic ``main_event``)."""
        self._store.publish(key, value)

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
    while True:
        try:
            await source.run(ctx)
            log.warning("source %s exited; restarting", source.key)
            failures = 0
        except asyncio.CancelledError:
            raise
        except Exception:
            delay = RESTART_BACKOFF_SECONDS[min(failures, len(RESTART_BACKOFF_SECONDS) - 1)]
            failures += 1
            log.exception("source %s crashed; restarting in %ss", source.key, delay)
            await asyncio.sleep(delay)
