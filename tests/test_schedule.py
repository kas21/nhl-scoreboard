"""`<sport>.schedule`: the slate for the next `show_games_within_days` days."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx

from scoreboard.data import SnapshotStore
from scoreboard.data.source import SourceContext
from scoreboard.nhl.api import BASE_URL, NhlApi
from scoreboard.nhl.schedule import fetch_weeks, schedule_games
from scoreboard.nhl.source import NhlConfig, NhlSource

FIX = Path(__file__).parent / "fixtures"


def load(sport, name):
    return json.loads((FIX / sport / name).read_text())


def test_nhl_schedule_games_follow_the_window():
    week = load("nhl", "schedule_now.json")                       # 2026-09-29 .. 2026-10-05
    assert schedule_games([week], {}, "2026-09-29", 0) and all(g["date"] == "2026-09-29" for g in schedule_games([week], {}, "2026-09-29", 0))
    two = schedule_games([week], {"TOR": "0-0-0"}, "2026-09-29", 2)
    assert sorted({g["date"] for g in two}) == ["2026-09-29", "2026-09-30", "2026-10-01"]
    assert len(two) == 5 + 3 + 8
    g = two[0]
    assert g["phase"] == "pregame" and g["date"] == "2026-09-29" and g["away"]["abbrev"] and g["start_time_utc"]
    assert all(g["date"] >= "2026-10-01" for g in schedule_games([week], {}, "2026-10-01", 30))
    assert schedule_games([week], {}, "2026-09-29", 30, follow_preseason=False) == [g for g in schedule_games([week], {}, "2026-09-29", 30) if g["type"] != 1]


@pytest.mark.asyncio
async def test_nhl_fetch_weeks_follows_next_start_date_only_as_far_as_needed():
    week = load("nhl", "schedule_now.json")
    calls = []

    def next_week(request):
        calls.append(request.url.path)
        start = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"gameWeek": [{"date": start, "games": []}], "nextStartDate": "2026-10-13" if start == "2026-10-06" else None})

    async with httpx.AsyncClient() as http, respx.mock(base_url=BASE_URL) as mock:
        mock.get(url__regex=r".*/schedule/2026-.*").mock(side_effect=next_week)
        api = NhlApi(http)
        assert len(await fetch_weeks(api, week, "2026-09-29", 2)) == 1 and calls == []
        assert len(await fetch_weeks(api, week, "2026-09-29", 7)) == 2 and calls == ["/v1/schedule/2026-10-06"]
        assert len(await fetch_weeks(api, week, "2026-09-29", 20)) == 3


@pytest.mark.asyncio
async def test_nhl_source_publishes_schedule(monkeypatch):
    import scoreboard.nhl.source as src
    monkeypatch.setattr(src, "_local_today", lambda ctx: "2026-09-29")
    store = SnapshotStore()
    cfg = NhlConfig(favorites=["TOR"], idle_interval=15, standings_interval=300, show_games_within_days=1)
    async with httpx.AsyncClient() as http, respx.mock(base_url=BASE_URL) as mock:
        mock.get("/score/now").mock(return_value=httpx.Response(200, json=load("nhl", "score_2026-04-11.json")))
        mock.get("/standings/now").mock(return_value=httpx.Response(200, json=load("nhl", "standings_2026-04-10.json")))
        mock.get("/club-schedule-season/TOR/now").mock(return_value=httpx.Response(200, json=load("nhl", "club_schedule_TOR_week.json")))
        mock.get("/schedule/now").mock(return_value=httpx.Response(200, json=load("nhl", "schedule_now.json")))
        ctx = SourceContext("nhl", store, lambda: cfg, http)
        task = asyncio.create_task(NhlSource().run(ctx))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if store.get().has("nhl.schedule"):
                break
        task.cancel()
        snap = store.get()
    sched = snap.get("nhl.schedule")
    assert sorted({g["date"] for g in sched}) == ["2026-09-29", "2026-09-30"] and len(sched) == 8
    assert sched[0]["home"]["record"].count("-") == 2                  # records filled from standings
