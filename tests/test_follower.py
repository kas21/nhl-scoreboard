"""A follower panel takes everything from the master's /api/snapshot and fetches nothing else."""
import asyncio

import httpx
import pytest
import respx

from scoreboard import follower as mod
from scoreboard.config.models import FollowerConfig
from scoreboard.data import SnapshotStore
from scoreboard.data.health import SourceHealth
from scoreboard.data.source import SourceContext
from scoreboard.follower import FollowerSource, master_url, teams_in

MASTER = "http://office.local:8080"
GAME = {"away": {"abbrev": "TOR"}, "home": {"abbrev": "MTL"}, "phase": "live"}


async def run_until(store, source, mock_setup, predicate, health=None, ticks=100):
    mod.MIN_ROUND_SECONDS = 0.001                   # the mock master answers at once; keep the loop polite but quick
    async with httpx.AsyncClient() as http, respx.mock(base_url=MASTER, assert_all_called=False) as mock:
        mock_setup(mock)
        ctx = SourceContext("follower", store, lambda: FollowerConfig(), http, health=health)
        task = asyncio.create_task(source.run(ctx))
        try:
            for _ in range(ticks):
                await asyncio.sleep(0.01)
                if predicate(store.get()):
                    break
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return mock


def cfg(**kw):
    return FollowerConfig(enabled=True, master_url=MASTER, **kw)


@pytest.mark.asyncio
async def test_relays_every_key_and_then_only_asks_for_changes(monkeypatch):
    monkeypatch.setattr(mod.logos, "prefetch", _no_prefetch)
    store = SnapshotStore()
    asked = []

    def master(request):
        since = int(request.url.params["since"])
        asked.append(since)
        if since < 0:
            return httpx.Response(200, json={"version": 7, "data": {"nhl.scores": [GAME], "system": {"online": True}, "weather.current": {"t": 2}}})
        if since == 7:
            return httpx.Response(200, json={"version": 9, "data": {"nhl.scores": [{**GAME, "phase": "final"}]}})
        return httpx.Response(200, json={"version": 9, "data": {}})

    await run_until(store, FollowerSource(cfg), lambda m: m.get("/api/snapshot").mock(side_effect=master),
                    lambda s: (s.get("nhl.scores") or [{}])[0].get("phase") == "final")
    snap = store.get()
    assert snap.get("weather.current") == {"t": 2} and snap.get("system") == {"online": True}
    assert asked[:2] == [-1, 7] and all(v == 9 for v in asked[2:])
    assert asked.count(7) == 1, "each version is asked for once; the cursor moves on"


@pytest.mark.asyncio
async def test_a_master_restart_is_noticed_and_resynced(monkeypatch):
    monkeypatch.setattr(mod.logos, "prefetch", _no_prefetch)
    store = SnapshotStore()
    calls = []

    def master(request):
        since = int(request.url.params["since"])
        calls.append(since)
        if len(calls) == 1:
            return httpx.Response(200, json={"version": 500, "data": {"a": 1}})
        if len(calls) == 2:
            return httpx.Response(200, json={"version": 3, "data": {}})       # younger than 500: it restarted
        return httpx.Response(200, json={"version": 3, "data": {"a": 2, "b": 1}})

    await run_until(store, FollowerSource(cfg), lambda m: m.get("/api/snapshot").mock(side_effect=master),
                    lambda s: s.get("b") == 1)
    assert calls[:3] == [-1, 500, -1]
    assert store.get().get("a") == 2


@pytest.mark.asyncio
async def test_losing_the_master_marks_the_panel_offline(monkeypatch):
    monkeypatch.setattr(mod.logos, "prefetch", _no_prefetch)
    monkeypatch.setattr(mod, "RETRY_DELAYS", (0,))
    store = SnapshotStore()
    health = SourceHealth()
    await run_until(store, FollowerSource(cfg), lambda m: m.get("/api/snapshot").mock(return_value=httpx.Response(503)),
                    lambda s: s.has("system"), health=health)
    assert store.get().get("system")["online"] is False
    assert health.get("follower").status == "offline"
    assert health.get("follower").last_url.startswith(MASTER)          # the diagnostics row says who we follow


@pytest.mark.asyncio
async def test_disabled_follower_stays_idle(monkeypatch):
    monkeypatch.setattr(mod, "IDLE_RECHECK_SECONDS", 0.01)
    store = SnapshotStore()
    mock = await run_until(store, FollowerSource(lambda: FollowerConfig(enabled=False, master_url=MASTER)),
                           lambda m: m.get("/api/snapshot").mock(return_value=httpx.Response(200, json={"version": 1, "data": {"a": 1}})),
                           lambda s: s.has("a"), ticks=10)
    assert not mock.calls and not store.get().data


@pytest.mark.asyncio
async def test_logos_are_fetched_for_the_teams_the_master_talks_about(monkeypatch):
    fetched = []

    async def fake_prefetch(http, sport, abbrevs, log):
        fetched.append((sport, abbrevs))
        return 0

    monkeypatch.setattr(mod.logos, "prefetch", fake_prefetch)
    store = SnapshotStore()
    data = {"nhl.scores": [GAME], "mlb.standings": {"teams": {"NYY": {}, "BOS": {}}}, "nfl.team_summary": {"BUF": {}}}
    await run_until(store, FollowerSource(cfg), lambda m: m.get("/api/snapshot").mock(return_value=httpx.Response(200, json={"version": 1, "data": data})),
                    lambda s: s.has("nfl.team_summary"))
    await asyncio.sleep(0.02)
    assert sorted(fetched) == [("mlb", ("BOS", "NYY")), ("nfl", ("BUF",)), ("nhl", ("MTL", "TOR"))]


def test_master_url_accepts_a_bare_host_and_trailing_slash():
    assert master_url(FollowerConfig(master_url="office.local:8080/")) == "http://office.local:8080/api/snapshot"
    assert master_url(FollowerConfig(master_url=" https://board.example ")) == "https://board.example/api/snapshot"
    assert master_url(FollowerConfig(master_url="")) is None


def test_teams_in_ignores_shapes_it_does_not_know():
    assert teams_in("nhl", {"nhl.scores": [GAME, "junk", {"away": None}], "nhl.standings": {"teams": "?"}}) == {"TOR", "MTL"}
    assert teams_in("nhl", {}) == set()


async def _no_prefetch(http, sport, abbrevs, log):
    return 0


@pytest.mark.asyncio
async def test_teams_that_arrive_during_a_logo_fetch_get_fetched_afterwards(monkeypatch):
    """The scores name two teams and a prefetch starts; the standings name thirty a moment
    later. Those used to wait for the *next* new team to show up."""
    batches = []
    release = asyncio.Event()

    async def slow_prefetch(http, sport, abbrevs, log):
        batches.append(set(abbrevs))
        if len(batches) == 1:
            await release.wait()
        return 0

    monkeypatch.setattr(mod.logos, "prefetch", slow_prefetch)
    src = mod.FollowerSource(lambda: FollowerConfig(enabled=True, master_url="http://m"))
    store = SnapshotStore()
    async with httpx.AsyncClient() as http:
        ctx = SourceContext("follower", store, mod._NoSettings, http)
        src._want_logos(ctx, {"nhl.scores": [{"away": {"abbrev": "TOR"}, "home": {"abbrev": "BOS"}}]})
        await asyncio.sleep(0)
        src._want_logos(ctx, {"nhl.standings": {"teams": {"TOR": {}, "BOS": {}, "MTL": {}, "OTT": {}}}})
        await asyncio.sleep(0)
        assert batches == [{"TOR", "BOS"}]                   # the second batch waits its turn
        release.set()
        for _ in range(20):
            await asyncio.sleep(0.01)
            if len(batches) == 2:
                break
        assert batches[1] == {"TOR", "BOS", "MTL", "OTT"}
        for t in src._logo_tasks.values():
            t.cancel()
