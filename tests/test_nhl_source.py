import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx

from scoreboard.data import SnapshotStore
from scoreboard.data.source import SourceContext
from scoreboard.nhl.api import BASE_URL
from scoreboard.nhl.source import NhlConfig, NhlSource

FIX = Path(__file__).parent / "fixtures" / "nhl"


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.mark.asyncio
async def test_source_publishes_scores_main_event_standings_and_summary(monkeypatch):
    import scoreboard.nhl.source as src
    monkeypatch.setattr(src, "_local_today", lambda ctx: "2026-04-11")     # fixture game day
    store = SnapshotStore()
    cfg = NhlConfig(favorites=["TOR"], idle_interval=15, standings_interval=300)
    async with httpx.AsyncClient() as http, respx.mock(base_url=BASE_URL) as mock:
        mock.get("/score/now").mock(return_value=httpx.Response(200, json=load("score_2026-04-11.json")))
        mock.get("/standings/now").mock(return_value=httpx.Response(200, json=load("standings_2026-04-10.json")))
        mock.get("/club-schedule-season/TOR/now").mock(return_value=httpx.Response(200, json=load("club_schedule_TOR_week.json")))
        ctx = SourceContext("nhl", store, lambda: cfg, http)
        task = asyncio.create_task(NhlSource().run(ctx))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if store.get().has("nhl.scores", "main_event", "nhl.standings", "nhl.team_summary"):
                break
        task.cancel()
        snap = store.get()
    assert len(snap.get("nhl.scores")) == 15
    assert snap.get("main_event")["home"]["abbrev"] == "TOR" and snap.get("main_event")["phase"] == "postgame"
    assert snap.get("main_event")["home"]["record"].count("-") == 2   # scores waited for standings
    assert "TOR" in snap.get("nhl.team_summary")
    assert snap.get("system")["online"] is True


@pytest.mark.asyncio
async def test_source_marks_offline_on_failure(monkeypatch):
    store = SnapshotStore()
    cfg = NhlConfig(favorites=["TOR"])
    import scoreboard.nhl.api as api_mod
    import scoreboard.nhl.source as src
    api_mod.RETRY_DELAYS = (0, 0, 0)
    monkeypatch.setattr(src, "OFFLINE_AFTER_FAILURES", 1)       # threshold itself is covered below
    async with httpx.AsyncClient() as http, respx.mock(base_url=BASE_URL) as mock:
        mock.get("/score/now").mock(return_value=httpx.Response(503))
        mock.get("/standings/now").mock(return_value=httpx.Response(503))
        ctx = SourceContext("nhl", store, lambda: cfg, http)
        task = asyncio.create_task(NhlSource().run(ctx))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if store.get().has("system"):
                break
        task.cancel()
    assert store.get().get("system")["online"] is False


def test_offline_threshold_is_several_failures():
    from scoreboard.nhl.source import OFFLINE_AFTER_FAILURES
    assert OFFLINE_AFTER_FAILURES >= 3
