import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from scoreboard.boards.base import BoardContext
from scoreboard.data import Event, SnapshotStore
from scoreboard.data.source import SourceContext
from scoreboard.extras.flights.board import NearbyBoard, NearbyConfig, OverheadBoard, OverheadConfig
from scoreboard.extras.flights.source import (
    FlightsConfig,
    FlightsSource,
    compass,
    detect_overhead,
    is_overhead,
    normalize_aircraft,
    parse_adsbdb,
    short_airline,
)
from scoreboard.render.profiles import profile_for

FIX = Path(__file__).parent / "fixtures" / "flights"


def load(name):
    return json.loads((FIX / name).read_text())


def test_normalize_and_helpers():
    raw = load("adsb_lol_point.json")["ac"]
    acs = [a for a in (normalize_aircraft(r) for r in raw) if a]
    assert acs and all("distance_km" in a for a in acs)
    assert compass(0) == "N" and compass(95) == "E" and compass(300) == "NW"
    assert short_airline("Air Canada") == "Air Canada" and short_airline("American Airlines") == "American"
    ground = normalize_aircraft({"hex": "abc", "lat": 1, "lon": 1, "alt_baro": "ground"})
    assert ground["on_ground"] and ground["altitude_ft"] is None


def test_parse_adsbdb_route():
    info = parse_adsbdb(load("adsbdb_callsign.json"))
    assert info["route"] == "YYZ-YVR" and info["airline"] == "Air Canada" and info["ident"] == "AC123"
    assert parse_adsbdb({"response": "unknown callsign"}) is None


def test_overhead_detection():
    cfg = FlightsConfig(overhead_km=3, overhead_max_alt_ft=8000)
    near = {"hex": "a", "distance_km": 1.2, "altitude_ft": 3000, "on_ground": False}
    high = {"hex": "b", "distance_km": 1.2, "altitude_ft": 30000, "on_ground": False}
    assert is_overhead(near, cfg) and not is_overhead(high, cfg)
    store = SnapshotStore()
    s0 = store.publish("flights.overhead", [])
    s1 = store.publish("flights.overhead", [near])
    s2 = store.publish("flights.overhead", [near])
    assert [e.kind for e in detect_overhead(s0, s1)] == ["flights.overhead"]
    assert list(detect_overhead(s1, s2)) == []        # same aircraft: no repeat alert


@pytest.mark.asyncio
async def test_source_polls_and_enriches():
    store = SnapshotStore()
    cfg = FlightsConfig(radius_km=40, max_aircraft=3, poll_seconds=10)
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(url__regex=r"https://api\.adsb\.lol/.*").mock(return_value=httpx.Response(200, json=load("adsb_lol_point.json")))
        mock.get(url__regex=r"https://api\.adsbdb\.com/.*").mock(return_value=httpx.Response(200, json=load("adsbdb_callsign.json")))
        ctx = SourceContext("flights", store, lambda: cfg, http)
        ctx.location = (43.65, -79.38)
        task = asyncio.create_task(FlightsSource().run(ctx))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if store.get().has("flights.nearby", "flights.overhead"):
                break
        task.cancel()
    nearby = store.get().get("flights.nearby")
    assert 1 <= len(nearby) <= 3
    assert nearby == sorted(nearby, key=lambda a: a["distance_km"])
    assert any(a["route"] == "YYZ-YVR" for a in nearby if a["callsign"])


def test_boards_render():
    ac = {**normalize_aircraft(load("adsb_lol_point.json")["ac"][0]), **parse_adsbdb(load("adsbdb_callsign.json"))}
    snap = SnapshotStore().publish("flights.nearby", [ac, ac])
    now = datetime(2026, 8, 26, 12, tzinfo=ZoneInfo("America/Toronto"))
    for w, h in [(128, 64), (64, 32)]:
        ctx = BoardContext(snapshot=snap, profile=profile_for(w, h), width=w, height=h, fps=30, now=now, elapsed=1.0)
        img = NearbyBoard().render(ctx, NearbyConfig())
        assert img.size == (w, h) and img.getbbox() is not None
    ev = Event("flights.overhead", payload={"aircraft": ac})
    ctx = BoardContext(snapshot=snap, profile=profile_for(128, 64), width=128, height=64, fps=30, now=now, elapsed=0.5, event=ev)
    board = OverheadBoard()
    assert board.matches(ev, OverheadConfig())
    assert board.render(ctx, OverheadConfig()).getbbox() is not None
    assert board.done(BoardContext(**{**ctx.__dict__, "elapsed": 8.1}), OverheadConfig())
