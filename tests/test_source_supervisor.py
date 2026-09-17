"""Turning a source on or off in the config takes effect at once, in both directions."""
import asyncio

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from scoreboard.data import SnapshotStore
from scoreboard.data.health import SourceHealth
from scoreboard.data.source import SourceContext, SourceSupervisor, source_enabled


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: bool = True
    interval: float = 30.0


class Fake:
    """Publishes a slate and a candidate game, then naps for ``interval`` (which a settings change cuts short)."""
    key = "nfl"
    config_model = Settings

    def __init__(self) -> None:
        self.polls = 0

    async def run(self, ctx: SourceContext) -> None:
        while True:
            self.polls += 1
            ctx.publish([{"id": self.polls}], subkey="scores")
            ctx.publish_to("nfl.main_event", {"id": self.polls, "sport": "nfl", "phase": "live"})
            await ctx.sleep(ctx.config.interval)


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_disable_retracts_and_enable_restarts():
    settings = {"nfl": {"enabled": True}}
    store, health = SnapshotStore(), SourceHealth()
    async with httpx.AsyncClient() as http:
        ctx = SourceContext("nfl", store, lambda: Settings(**settings["nfl"]), http, health=health)
        ctx.bind(asyncio.get_running_loop())
        src = Fake()
        sup = SourceSupervisor({"nfl": src}, {"nfl": ctx}, store, health)
        await sup.reconcile(settings)
        await settle()
        assert sup.running == {"nfl"} and src.polls == 1
        assert store.get().get("nfl.scores") == [{"id": 1}] and store.get().get("nfl.main_event")["id"] == 1
        assert health.get("nfl").status == "ok"

        settings["nfl"] = {"enabled": False}
        await sup.reconcile(settings)
        assert sup.running == set()
        snap = store.get()
        assert snap.get("nfl.scores") is None and snap.get("nfl.main_event") is None      # retracted, not just stale
        assert "nfl.scores" in snap.data and snap.versions["nfl.scores"] > 1               # as a publish, so followers see it
        assert health.get("nfl").status == "disabled"

        settings["nfl"] = {"enabled": True}
        await sup.reconcile(settings)
        await settle()
        assert sup.running == {"nfl"} and src.polls == 2 and store.get().get("nfl.scores") == [{"id": 2}]
        assert health.get("nfl").status == "ok"
        await sup.shutdown()
        assert sup.running == set()


@pytest.mark.asyncio
async def test_a_settings_change_wakes_the_source_and_an_unrelated_save_does_not():
    settings = {"nfl": {"enabled": True, "interval": 30}}
    store = SnapshotStore()
    async with httpx.AsyncClient() as http:
        ctx = SourceContext("nfl", store, lambda: Settings(**settings["nfl"]), http)
        ctx.bind(asyncio.get_running_loop())
        src = Fake()
        sup = SourceSupervisor({"nfl": src}, {"nfl": ctx}, store)
        await sup.reconcile(settings)
        await settle()
        assert src.polls == 1
        await sup.reconcile(settings)                       # a save that changed something else entirely
        await settle()
        assert src.polls == 1
        settings["nfl"] = {"enabled": True, "interval": 45}
        await sup.reconcile(settings)                       # this one touched the source: the nap ends now
        await settle()
        assert src.polls == 2
        await sup.shutdown()


@pytest.mark.asyncio
async def test_a_source_that_starts_disabled_is_reported_off_not_starting():
    store, health = SnapshotStore(), SourceHealth()
    async with httpx.AsyncClient() as http:
        ctx = SourceContext("nfl", store, lambda: Settings(enabled=False), http, health=health)
        sup = SourceSupervisor({"nfl": Fake()}, {"nfl": ctx}, store, health)
        await sup.reconcile({"nfl": {"enabled": False}})
        assert sup.running == set() and health.get("nfl").status == "disabled"
        assert not source_enabled(ctx)


def test_retract_only_touches_the_owners_keys_and_reports_them():
    store = SnapshotStore()
    store.publish("nfl.scores", [1], owner="nfl")
    store.publish("nfl.main_event", {"id": 1}, owner="nfl")
    store.publish("nhl.scores", [2], owner="nhl")
    seen = []
    store.subscribe(lambda prev, new: seen.append(new.version))
    assert store.retract("nfl") == ["nfl.main_event", "nfl.scores"]
    snap = store.get()
    assert snap.get("nfl.scores") is None and snap.get("nfl.main_event") is None and snap.get("nhl.scores") == [2]
    assert len(seen) == 2
    assert store.retract("nfl") == [] and store.retract("nobody") == []


@pytest.mark.asyncio
async def test_sleep_ends_early_on_wake_and_on_time_otherwise():
    store = SnapshotStore()
    async with httpx.AsyncClient() as http:
        ctx = SourceContext("x", store, lambda: Settings(), http)
        ctx.bind(asyncio.get_running_loop())
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        loop.call_later(0.01, ctx.wake)
        await ctx.sleep(5)
        assert loop.time() - t0 < 1
        t0 = loop.time()
        await ctx.sleep(0.02)
        assert loop.time() - t0 >= 0.015


@pytest.mark.asyncio
async def test_a_source_that_returns_at_once_yields_to_the_loop():
    """A run() that comes straight back is restarted with backoff, not spun in a tight loop
    that would starve every other task."""
    from scoreboard.data.source import run_source_forever

    class Instant:
        key = "nfl"
        config_model = Settings
        runs = 0

        async def run(self, ctx):
            self.runs += 1

    store = SnapshotStore()
    async with httpx.AsyncClient() as http:
        ctx = SourceContext("nfl", store, Settings, http)
        src = Instant()
        task = asyncio.create_task(run_source_forever(src, ctx))
        try:
            await asyncio.wait_for(asyncio.sleep(0.05), timeout=1)     # the loop must get this turn
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert src.runs == 1


@pytest.mark.asyncio
async def test_a_wake_that_lands_mid_fetch_is_not_lost():
    """A settings save while the source is awaiting its HTTP call used to be cleared by the
    nap that followed, which then ran its full length. Both loops of a source see the wake."""
    store = SnapshotStore()
    async with httpx.AsyncClient() as http:
        ctx = SourceContext("nfl", store, Settings, http)
        ctx.bind(asyncio.get_running_loop())
        ctx.wake()                                       # arrives while "fetching"
        t0 = asyncio.get_running_loop().time()
        await ctx.sleep(5)
        assert asyncio.get_running_loop().time() - t0 < 1
        t0 = asyncio.get_running_loop().time()
        await ctx.nap(0.05)                              # consumed: this one runs its length
        assert asyncio.get_running_loop().time() - t0 >= 0.04
        main, side = asyncio.create_task(ctx.sleep(5)), asyncio.create_task(ctx.nap(5))
        await asyncio.sleep(0)
        ctx.wake()
        await asyncio.wait_for(asyncio.gather(main, side), timeout=1)
