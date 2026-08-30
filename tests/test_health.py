import asyncio

import httpx
import pytest
import respx

from scoreboard.data import SnapshotStore
from scoreboard.data.health import SourceHealth, SourceStats
from scoreboard.data.source import SourceContext, run_source_forever


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_stats_start_unknown():
    h = SourceHealth(clock=Clock())
    h.register("nhl")
    s = h.get("nhl")
    assert isinstance(s, SourceStats)
    assert s.status == "starting"
    assert s.fetches == 0 and s.publishes == 0 and s.running is False


def test_fetch_success_and_failure_streaks():
    clock = Clock()
    h = SourceHealth(clock=clock)
    h.register("nhl")
    h.record_fetch("nhl", ok=True, latency_ms=12.5)
    s = h.get("nhl")
    assert s.fetches == 1 and s.failures == 0 and s.error_streak == 0
    assert s.last_ok_at == 1000.0 and s.last_latency_ms == 12.5
    assert s.status == "ok"

    clock.t = 1010.0
    for _ in range(3):
        h.record_fetch("nhl", ok=False, latency_ms=50.0, error="503 Service Unavailable")
    s = h.get("nhl")
    assert s.fetches == 4 and s.failures == 3 and s.error_streak == 3
    assert s.last_error == "503 Service Unavailable" and s.last_error_at == 1010.0
    assert s.last_ok_at == 1000.0                     # untouched by failures
    assert s.status == "offline"

    h.record_fetch("nhl", ok=True, latency_ms=8.0)
    assert h.get("nhl").error_streak == 0
    assert h.get("nhl").status == "ok"


def test_degraded_before_offline_threshold():
    h = SourceHealth(clock=Clock())
    h.register("nhl")
    h.record_fetch("nhl", ok=False, latency_ms=1.0, error="boom")
    assert h.get("nhl").status == "degraded"


def test_updates_are_immutable():
    h = SourceHealth(clock=Clock())
    h.register("nhl")
    before = h.get("nhl")
    h.record_publish("nhl", "nhl.scores")
    after = h.get("nhl")
    assert before is not after
    assert before.publishes == 0 and after.publishes == 1
    assert after.keys == ("nhl.scores",)


def test_running_crash_and_restart_counters():
    clock = Clock()
    h = SourceHealth(clock=clock)
    h.register("nhl")
    h.set_running("nhl", True)
    assert h.get("nhl").running is True and h.get("nhl").started_at == 1000.0
    h.record_crash("nhl", "ZeroDivisionError: division by zero")
    s = h.get("nhl")
    assert s.running is False and s.restarts == 1
    assert s.last_error == "ZeroDivisionError: division by zero"
    assert s.status == "crashed"


def test_next_poll_and_snapshot_dict():
    clock = Clock()
    h = SourceHealth(clock=clock)
    h.register("nhl")
    h.set_next_poll("nhl", 1060.0)
    clock.t = 1030.0
    d = h.get("nhl").to_dict(now=clock())
    assert d["next_poll_in"] == 30.0
    assert d["key"] == "nhl" and d["status"] == "starting"
    assert h.all() == {"nhl": h.get("nhl")}


def test_unknown_key_is_ignored_not_raised():
    h = SourceHealth(clock=Clock())
    h.record_fetch("ghost", ok=True, latency_ms=1.0)     # a plugin we never registered
    assert h.get("ghost") is None


# -- wiring through SourceContext -------------------------------------------------


@pytest.mark.asyncio
async def test_context_tracks_http_publish_and_sleep():
    clock = Clock()
    health = SourceHealth(clock=clock)
    store = SnapshotStore()
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get("https://example.test/ok").mock(return_value=httpx.Response(200, json={}))
        mock.get("https://example.test/bad").mock(return_value=httpx.Response(503))
        mock.get("https://example.test/boom").mock(side_effect=httpx.ConnectError("no route"))
        ctx = SourceContext("demo", store, lambda: None, http, health=health)

        r = await ctx.http.get("https://example.test/ok")
        assert r.status_code == 200
        s = health.get("demo")
        assert s.fetches == 1 and s.failures == 0 and s.last_url == "https://example.test/ok"

        r = await ctx.http.get("https://example.test/bad")
        assert r.status_code == 503                        # response still returned to the caller
        s = health.get("demo")
        assert s.failures == 1 and "503" in s.last_error

        with pytest.raises(httpx.ConnectError):
            await ctx.http.get("https://example.test/boom")
        s = health.get("demo")
        assert s.failures == 2 and "no route" in s.last_error

        ctx.publish({"x": 1}, subkey="latest")
        assert health.get("demo").publishes == 1
        assert health.get("demo").keys == ("demo.latest",)

        task = asyncio.create_task(ctx.sleep(30))
        await asyncio.sleep(0)
        assert health.get("demo").next_poll_at == pytest.approx(clock() + 30)
        task.cancel()


@pytest.mark.asyncio
async def test_context_without_health_still_works():
    store = SnapshotStore()
    async with httpx.AsyncClient() as http:
        ctx = SourceContext("demo", store, lambda: None, http)
        ctx.publish(1)
        task = asyncio.create_task(ctx.sleep(10))
        await asyncio.sleep(0)
        task.cancel()
    assert store.get().get("demo") == 1


@pytest.mark.asyncio
async def test_runner_records_running_and_crashes(monkeypatch):
    import scoreboard.data.source as source_mod

    monkeypatch.setattr(source_mod, "RESTART_BACKOFF_SECONDS", (0, 0))
    health = SourceHealth(clock=Clock())
    store = SnapshotStore()
    calls = {"n": 0}

    class Flaky:
        key = "flaky"
        config_model = None

        async def run(self, ctx):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first run explodes")
            await asyncio.Event().wait()

    async with httpx.AsyncClient() as http:
        ctx = SourceContext("flaky", store, lambda: None, http, health=health)
        task = asyncio.create_task(run_source_forever(Flaky(), ctx))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if calls["n"] == 2:
                break
        s = health.get("flaky")
        assert s.running is True
        assert s.restarts == 1
        assert "first run explodes" in s.last_error
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert health.get("flaky").running is False
