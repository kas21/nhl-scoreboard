"""Runs the simulations: claims their keys, ticks their clocks, publishes what they say.

One hub per app. Any number of engines can run at once as long as they claim different
keys (an NHL game and a weather alert together is the interesting case); a second engine
wanting a key the first holds is refused with ``ClaimError`` and nothing is started.

The web API drives this from its worker threads and ``run()`` ticks it from the asyncio
loop, so every entry point takes the lock. Publishing happens *after* the lock is let go:
the store fans a publish out to the detectors and the arbiter, and holding our lock
across code we do not own would let a slow listener stall the API.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from ..config import AppConfig
from ..data import SnapshotStore
from ..data.store import ClaimError
from .base import SimContext, SimError, Simulation, sim_schema

log = logging.getLogger(__name__)

TICK_SECONDS = 0.25         # how often running engines are advanced (a 1 Hz clock needs less)
IDLE_SECONDS = 1.0          # poll cadence with nothing running; only costs a wake-up

__all__ = ["ClaimError", "SimError", "SimulatorHub", "ValidationError"]


class SimulatorHub:
    def __init__(self, store: SnapshotStore, config_getter: Callable[[], AppConfig],
                 sims: dict[str, Simulation], clock: Callable[[], float] = time.monotonic) -> None:
        self._store = store
        self._config = config_getter
        self._sims = dict(sims)
        self._clock = clock
        self._lock = threading.RLock()
        self._running: dict[str, BaseModel] = {}      # key -> the options it was started with
        self._started_at: dict[str, float] = {}

    # -- introspection ---------------------------------------------------------

    @property
    def active(self) -> bool:
        return bool(self._running)

    def running(self) -> list[str]:
        with self._lock:
            return sorted(self._running)

    def state(self) -> dict[str, Any]:
        """Everything the page needs, in one poll: each engine's form, whether it runs, and
        if so its options, a summary and the buttons that apply right now."""
        with self._lock:
            sims = []
            for key, sim in self._sims.items():
                entry: dict[str, Any] = {
                    "key": key, "title": sim.title, "description": sim.description,
                    "claims": sorted(sim.claims), "schema": sim_schema(sim), "running": key in self._running,
                }
                if key in self._running:
                    entry.update({
                        "options": self._running[key].model_dump(mode="json"),
                        "since": self._clock() - self._started_at[key],
                        "state": sim.describe(),
                        "actions": [a.to_dict() for a in sim.actions()],
                    })
                sims.append(entry)
            return {"active": bool(self._running), "sims": sims}

    # -- control -------------------------------------------------------------

    def start(self, key: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Start (or restart with new options) the engine ``key``. Raises ``KeyError`` for an
        unknown engine, ``ValidationError`` for bad options, ``ClaimError`` for a key another
        running engine holds."""
        sim = self._sims[key]
        opts = sim.options_model.model_validate(options or {})
        with self._lock:
            if key in self._running:
                self._stop_locked(key)
            self._store.claim(sim.claims, owner=self._owner(key))
            try:
                sim.start(opts, self._context())
            except Exception:
                self._store.release(sim.claims, owner=self._owner(key))
                raise
            self._running[key] = opts
            self._started_at[key] = self._clock()
            values = sim.values()
        log.info("simulation %s started: %s", key, opts.model_dump(mode="json"))
        self._publish(key, values)
        return self.state()

    def stop(self, key: str) -> dict[str, Any]:
        with self._lock:
            if key in self._running:
                self._stop_locked(key)
        return self.state()

    def stop_all(self) -> dict[str, Any]:
        with self._lock:
            for key in list(self._running):
                self._stop_locked(key)
        return self.state()

    def _stop_locked(self, key: str) -> None:
        self._running.pop(key, None)
        self._started_at.pop(key, None)
        log.info("simulation %s stopped", key)
        # Releasing publishes the real data back; the store runs the listeners for that, so
        # do it under our lock only because it is cheap and the alternative (a tick landing
        # between the pop and the release) would publish a stopped engine's values.
        self._store.release(self._sims[key].claims, owner=self._owner(key))

    def action(self, key: str, name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Apply a button press. ``SimError`` for one the engine refuses; ``KeyError`` when
        the engine is not running."""
        with self._lock:
            if key not in self._running:
                raise KeyError(f"simulation {key!r} is not running")
            sim = self._sims[key]
            sim.action(name, dict(params or {}), self._context())
            values = sim.values()
        self._publish(key, values)
        return self.state()

    # -- pacing -----------------------------------------------------------------

    def tick(self, now: float | None = None) -> None:
        """Advance every running engine and publish the ones that changed."""
        changed: list[tuple[str, dict[str, Any]]] = []
        with self._lock:
            if not self._running:
                return
            ctx = self._context(now)
            for key in list(self._running):
                sim = self._sims[key]
                try:
                    if sim.tick(ctx):
                        changed.append((key, sim.values()))
                except Exception:
                    log.exception("simulation %s failed while ticking; stopping it", key)
                    self._stop_locked(key)
        for key, values in changed:
            self._publish(key, values)

    async def run(self) -> None:
        """Tick forever from the asyncio loop; the hub is idle-cheap with nothing running."""
        while True:
            self.tick()
            await asyncio.sleep(TICK_SECONDS if self._running else IDLE_SECONDS)

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _owner(key: str) -> str:
        return f"sim:{key}"

    def _context(self, now: float | None = None) -> SimContext:
        cfg = self._config()
        try:
            wall = datetime.now(ZoneInfo(cfg.location.timezone))
        except Exception:
            wall = datetime.now().astimezone()
        return SimContext(now=self._clock() if now is None else now, wall=wall, snapshot=self._store.get(), config=cfg)

    def _publish(self, key: str, values: dict[str, Any]) -> None:
        owner = self._owner(key)
        claims = self._sims[key].claims
        for k, v in values.items():
            if k not in claims:
                log.warning("simulation %s tried to publish %s, which it did not claim; dropped", key, k)
                continue
            self._store.publish(k, v, owner=owner)
