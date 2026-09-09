"""Weather alerts: NWS / Environment Canada parsing, selection, the detector, the source and both boards."""
import asyncio
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from scoreboard.boards.base import BoardContext
from scoreboard.data import Event, SnapshotStore
from scoreboard.data.events import EventBus
from scoreboard.data.source import SourceContext
from scoreboard.extras.weather.alerts.board import (
    LEVEL_COLORS,
    AlertBoard,
    AlertBoardConfig,
    AlertsBoard,
    AlertsBoardConfig,
    until_text,
)
from scoreboard.extras.weather.alerts.eccc import ECCC_ALERTS, parse_eccc
from scoreboard.extras.weather.alerts.model import (
    OutOfBounds,
    classify_event,
    link_updates,
    make_alert,
    select_alerts,
)
from scoreboard.extras.weather.alerts.nws import NWS_ALERTS, parse_nws
from scoreboard.extras.weather.alerts.source import (
    WeatherAlertsConfig,
    WeatherAlertsSource,
    detect_alerts,
)
from scoreboard.plugins import load_registry
from scoreboard.render.profiles import profile_for

FIX = Path(__file__).parent / "fixtures" / "weather"
TORONTO = ZoneInfo("America/Toronto")
NOW = datetime(2026, 9, 8, 17, 0, tzinfo=UTC)          # every fixture alert is in force
LATER = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)         # the Red Flag Warning and Flood Watch have ended
OUT_OF_BOUNDS = {"title": "Invalid Parameter", "status": 400, "detail": 'Parameter "point" is invalid: out of bounds'}


def load(name):
    return json.loads((FIX / name).read_text())


def alert(event="Tornado Warning", area="Erie, NY", **extra):
    level, name = classify_event(event)
    return {"id": f"id-{event}-{area}", "key": event.lower(), "provider": "nws", "event": event,
            "name": name, "level": level, "severity": "Extreme", "urgency": "Immediate", "headline": f"{event} until 4:45 PM",
            "summary": "At 412 PM EDT, a severe thunderstorm capable of producing a tornado was located near Buffalo.",
            "area": area, "onset": "2026-09-08T16:12:00-04:00", "expires": "2026-09-08T16:45:00-04:00", "sender": "NWS Buffalo NY",
            **extra}


# -- parsing ---------------------------------------------------------------------


def test_classify_event_by_its_last_word():
    assert classify_event("Tornado Warning") == ("warning", "Tornado")
    assert classify_event("Winter Storm Watch") == ("watch", "Winter Storm")
    assert classify_event("Wind Advisory") == ("advisory", "Wind")
    assert classify_event("Special Weather Statement") == ("statement", "Special Weather")
    assert classify_event("Air Quality Alert") == ("other", "Air Quality Alert")


def test_nws_parse():
    alerts = parse_nws(load("nws_alerts.json"))
    assert [a["event"] for a in alerts] == ["Extreme Heat Warning", "Flood Watch", "Heat Advisory", "Small Craft Advisory",
                                            "Special Weather Statement", "Red Flag Warning"]
    heat = alerts[0]
    assert heat["level"] == "warning" and heat["name"] == "Extreme Heat" and heat["severity"] == "Severe" and heat["provider"] == "nws"
    assert heat["area"].startswith("Catalina") and heat["expires"] == "2026-09-10T20:00:00-07:00" and heat["sender"] == "NWS Los Angeles/Oxnard CA"
    assert heat["summary"] and "\n" not in heat["summary"] and len(heat["summary"]) <= 200
    assert heat["key"] == heat["id"] and heat["references"] == []          # identity follows the agency's message chain
    assert heat["headline"].upper() == heat["headline"]          # NWS's short all-caps headline when there is one
    assert alerts[1]["level"] == "watch" and alerts[2]["level"] == "advisory" and alerts[4]["level"] == "statement"


def test_nws_parse_drops_tests_and_cancellations():
    payload = load("nws_alerts.json")
    feats = payload["features"]
    feats[0]["properties"]["status"] = "Test"
    feats[1]["properties"]["messageType"] = "Cancel"
    assert [a["event"] for a in parse_nws({"features": feats})][:2] == ["Heat Advisory", "Small Craft Advisory"]
    assert parse_nws({}) == [] and parse_nws({"features": [{"properties": {}}]}) == []


def test_nws_updates_keep_the_identity_of_the_alert_they_supersede():
    """An NWS update is a new message (new id) referencing the old one; a second warning of the
    same kind with no references is a new hazard, so it gets its own card and interrupt."""
    original = make_alert(id="a", provider="nws", event="Tornado Warning", severity="Extreme", headline="", summary="",
                          area="Erie", onset=None, expires=None, sender="", key="a")
    update = make_alert(id="b", provider="nws", event="Tornado Warning", severity="Extreme", headline="", summary="",
                        area="Erie", onset=None, expires=None, sender="", key="b", references=["a"])
    later = make_alert(id="c", provider="nws", event="Tornado Warning", severity="Extreme", headline="", summary="",
                       area="Erie", onset=None, expires=None, sender="", key="c", references=["b"])
    fresh = make_alert(id="d", provider="nws", event="Tornado Warning", severity="Extreme", headline="", summary="",
                       area="Niagara", onset=None, expires=None, sender="", key="d")
    assert [a["key"] for a in link_updates([update, fresh], [original])] == ["a", "d"]
    assert [a["key"] for a in link_updates([later], [update])] == ["b"]                 # unknown chain: earliest reference
    assert [a["key"] for a in link_updates([later], link_updates([update], [original]))] == ["a"]
    linked = link_updates([update, fresh], [original])
    assert [a["key"] for a in select_alerts(linked, WeatherAlertsConfig(), NOW)] == ["a", "d"]   # two cards, not one
    payload = load("nws_alerts.json")
    feat = payload["features"][0]
    feat["properties"]["messageType"] = "Update"
    feat["properties"]["references"] = [{"identifier": "new", "sent": "2026-09-08T12:00:00-07:00"},
                                        {"identifier": "root", "sent": "2026-09-07T12:00:00-07:00"}]
    assert parse_nws(payload)[0]["references"] == ["root", "new"]
    assert parse_eccc(load("eccc_alerts.json"))[0]["key"] == "aqw"                       # ECCC has no chain: the hazard code


def test_eccc_parse():
    alerts = parse_eccc(load("eccc_alerts.json"))
    assert [a["event"] for a in alerts] == ["Air Quality Warning", "Frost Advisory"]          # the ended one is dropped
    aq = alerts[0]
    assert aq["level"] == "warning" and aq["name"] == "Air Quality" and aq["provider"] == "eccc"
    assert aq["area"].startswith("Ft. Simpson") and aq["expires"] == "2026-09-10T11:02:00.000Z"
    assert aq["summary"] and "\n" not in aq["summary"]
    assert aq["severity"] == "Moderate"                                                       # yellow -> Moderate
    assert alerts[1]["level"] == "advisory"


# -- selection --------------------------------------------------------------------


def test_select_filters_orders_and_dedupes():
    alerts = parse_nws(load("nws_alerts.json"))
    kept = select_alerts(alerts, WeatherAlertsConfig(), NOW)
    # marine advisory ignored, statement below the default minimum; warnings first, the soonest to expire ahead
    assert [a["event"] for a in kept] == ["Red Flag Warning", "Extreme Heat Warning", "Flood Watch", "Heat Advisory"]
    assert [a["event"] for a in select_alerts(alerts, WeatherAlertsConfig(), LATER)] == ["Extreme Heat Warning", "Heat Advisory"]
    everything = select_alerts(alerts, WeatherAlertsConfig(min_level="statement", ignore=[]), NOW)
    assert len(everything) == 6 and everything[-1]["event"] == "Special Weather Statement"
    assert [a["event"] for a in select_alerts(alerts, WeatherAlertsConfig(min_level="warning"), NOW)] == ["Red Flag Warning", "Extreme Heat Warning"]
    assert [a["event"] for a in select_alerts(alerts, WeatherAlertsConfig(ignore=["heat", "flood", "red flag"]), NOW)] == ["Small Craft Advisory"]
    twice = [alert(id="a"), alert(id="b", area="Niagara, NY"), alert("Wind Advisory")]
    assert [a["id"] for a in select_alerts(twice, WeatherAlertsConfig(), NOW)] == ["a", "id-Wind Advisory-Erie, NY"]
    smoke = [alert("Air Quality Alert", severity="Unknown"), alert("Wind Advisory", severity="Minor")]
    assert [a["event"] for a in select_alerts(smoke, WeatherAlertsConfig(), NOW)] == ["Wind Advisory", "Air Quality Alert"]
    assert select_alerts(smoke, WeatherAlertsConfig(min_level="watch"), NOW) == []


# -- detector ---------------------------------------------------------------------


def test_detector_fires_once_per_new_alert_and_flags_live_games():
    store = SnapshotStore()
    boot = store.get()
    s0 = store.publish("weather.alerts", [])
    assert list(detect_alerts(boot, s0)) == [] and list(detect_alerts(boot, s0.with_value("weather.alerts", [alert()]))) == []
    s1 = store.publish("weather.alerts", [alert()])
    s2 = store.publish("weather.alerts", [alert(area="Niagara, NY")])           # the NWS trimmed the county list
    events = detect_alerts(s0, s1)
    assert [e.kind for e in events] == ["weather.alert"] and events[0].payload["alert"]["event"] == "Tornado Warning"
    assert events[0].payload["live_game"] is False
    assert list(detect_alerts(s1, s2)) == []
    store.publish("main_event", {"phase": "live", "sport": "nhl"})
    s3 = store.publish("weather.alerts", [alert(), alert("Wind Advisory")])
    fresh = list(detect_alerts(s2, s3))
    assert [e.payload["alert"]["event"] for e in fresh] == ["Wind Advisory"] and fresh[0].payload["live_game"] is True
    s4 = store.publish("weather.alerts", None)                                   # source switched off / moved
    s5 = store.publish("weather.alerts", [alert(), alert("Wind Advisory")])
    assert list(detect_alerts(s3, s4)) == [] and list(detect_alerts(s4, s5)) == []


def test_detector_leaves_the_top_alert_standing_after_the_bus_collapses_a_burst():
    """Two alerts in one poll: the bus keeps the last event per kind, so the most serious is emitted last."""
    store, bus = SnapshotStore(), EventBus()
    store.subscribe(bus.on_snapshot)
    bus.register(detect_alerts)
    store.publish("weather.alerts", [])
    store.publish("weather.alerts", [alert("Tornado Warning"), alert("Wind Advisory")])
    events = bus.drain()
    assert len(events) == 1 and events[0].payload["alert"]["event"] == "Tornado Warning"


# -- source -----------------------------------------------------------------------


async def _run(source, config, routes):
    store = SnapshotStore()
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        for pattern, response in routes.items():
            mock.get(url__regex=pattern).mock(return_value=response)
        ctx = SourceContext("weather_alerts", store, lambda: config, http)
        ctx.location, ctx.timezone = (42.8864, -78.8784), "America/Toronto"
        task = asyncio.create_task(source.run(ctx))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if store.get().has("weather.alerts"):
                break
        task.cancel()
        calls = {i: route.call_count for i, route in enumerate(mock.routes)}
    return store.get(), calls


@pytest.mark.asyncio
async def test_source_polls_nws_and_publishes_selected_alerts():
    src = WeatherAlertsSource(clock=lambda: NOW)
    snap, _ = await _run(src, WeatherAlertsConfig(), {r"https://api\.weather\.gov/alerts/active.*": httpx.Response(200, json=load("nws_alerts.json"))})
    assert [a["event"] for a in snap.get("weather.alerts")] == ["Red Flag Warning", "Extreme Heat Warning", "Flood Watch", "Heat Advisory"]
    assert src.active_provider == "nws"


@pytest.mark.asyncio
async def test_source_falls_back_to_environment_canada_when_nws_says_out_of_bounds():
    src = WeatherAlertsSource(clock=lambda: NOW)
    snap, _ = await _run(src, WeatherAlertsConfig(), {
        r"https://api\.weather\.gov/alerts/active.*": httpx.Response(400, json=OUT_OF_BOUNDS),
        r"https://api\.weather\.gc\.ca/collections/weather-alerts/items.*": httpx.Response(200, json=load("eccc_alerts.json")),
    })
    assert [a["event"] for a in snap.get("weather.alerts")] == ["Air Quality Warning", "Frost Advisory"]
    assert src.active_provider == "eccc"


@pytest.mark.asyncio
async def test_source_honours_an_explicit_provider_and_publishes_empty_lists():
    src = WeatherAlertsSource(clock=lambda: NOW)
    snap, calls = await _run(src, WeatherAlertsConfig(provider="eccc"), {
        r"https://api\.weather\.gov/alerts/active.*": httpx.Response(200, json=load("nws_alerts.json")),
        r"https://api\.weather\.gc\.ca/collections/weather-alerts/items.*": httpx.Response(200, json={"type": "FeatureCollection", "features": []}),
    })
    assert snap.get("weather.alerts") == [] and src.active_provider == "eccc"
    assert sum(calls.values()) == 1


@pytest.mark.asyncio
async def test_a_failed_poll_still_retires_lapsed_alerts():
    clock = {"now": NOW}
    src = WeatherAlertsSource(clock=lambda: clock["now"])
    store = SnapshotStore()
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r"https://api\.weather\.gov/alerts/active.*").mock(return_value=httpx.Response(200, json=load("nws_alerts.json")))
        ctx = SourceContext("weather_alerts", store, lambda: WeatherAlertsConfig(), http)
        ctx.location = (42.8864, -78.8784)
        await src.poll(ctx, WeatherAlertsConfig(), ctx.location)
        assert len(store.get().get("weather.alerts")) == 4
        route.mock(return_value=httpx.Response(503))
        clock["now"] = LATER
        await src.poll(ctx, WeatherAlertsConfig(), ctx.location)
        assert [a["event"] for a in store.get().get("weather.alerts")] == ["Extreme Heat Warning", "Heat Advisory"]
        version = store.get().version
        await src.poll(ctx, WeatherAlertsConfig(), ctx.location)
        assert store.get().version == version                       # nothing changed: nothing republished


@pytest.mark.asyncio
async def test_a_failed_first_poll_does_not_arm_the_detector():
    """Boot before the network is up: nothing is published, so the first good poll is the
    baseline and hours-old warnings do not interrupt as news."""
    src = WeatherAlertsSource(clock=lambda: NOW)
    store, bus = SnapshotStore(), EventBus()
    store.subscribe(bus.on_snapshot)
    bus.register(detect_alerts)
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r"https://api\.weather\.gov/alerts/active.*").mock(side_effect=httpx.ConnectError("down"))
        ctx = SourceContext("weather_alerts", store, lambda: WeatherAlertsConfig(), http)
        ctx.location = (42.8864, -78.8784)
        await src.poll(ctx, WeatherAlertsConfig(), ctx.location)
        assert not store.get().has("weather.alerts")
        route.mock(return_value=httpx.Response(200, json=load("nws_alerts.json")))
        await src.poll(ctx, WeatherAlertsConfig(), ctx.location)
        assert len(store.get().get("weather.alerts")) == 4 and list(bus.drain()) == []


@pytest.mark.asyncio
async def test_switching_off_or_moving_withdraws_the_baseline():
    """Off, or at a new location, the board comes down and the next good poll is not news."""
    src = WeatherAlertsSource(clock=lambda: NOW)
    store, bus = SnapshotStore(), EventBus()
    store.subscribe(bus.on_snapshot)
    bus.register(detect_alerts)
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r"https://api\.weather\.gov/alerts/active.*").mock(return_value=httpx.Response(200, json=load("nws_alerts.json")))
        ctx = SourceContext("weather_alerts", store, lambda: WeatherAlertsConfig(), http)
        ctx.location = (42.8864, -78.8784)
        await src.poll(ctx, WeatherAlertsConfig(), ctx.location)
        assert len(store.get().get("weather.alerts")) == 4
        ctx.location = (43.65, -79.38)
        await src.poll(ctx, WeatherAlertsConfig(), ctx.location)
        assert len(store.get().get("weather.alerts")) == 4 and list(bus.drain()) == []
        ctx._config_getter = lambda: WeatherAlertsConfig(enabled=False)
        task = asyncio.create_task(src.run(ctx))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if store.get().get("weather.alerts") is None:
                break
        task.cancel()
        assert store.get().has("weather.alerts") and store.get().get("weather.alerts") is None
        ctx._config_getter = lambda: WeatherAlertsConfig()
        await src.poll(ctx, WeatherAlertsConfig(), ctx.location)
        assert len(store.get().get("weather.alerts")) == 4 and list(bus.drain()) == []


@pytest.mark.asyncio
async def test_source_wakes_when_the_soonest_alert_lapses():
    """Between polls the source sleeps only until the next expiry, then re-filters, so the
    board is empty (and out of the playlist) the moment nothing is in force."""
    ends = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)                      # Red Flag Warning and Flood Watch end
    clock = {"now": ends - timedelta(minutes=2)}
    src = WeatherAlertsSource(clock=lambda: clock["now"])
    store = SnapshotStore()
    naps, fetch_in = [], []

    async def sleep(seconds, until_poll=None):
        naps.append(seconds)
        fetch_in.append(until_poll)
        clock["now"] = LATER
        if len(naps) > 1:
            raise asyncio.CancelledError

    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r"https://api\.weather\.gov/alerts/active.*").mock(return_value=httpx.Response(200, json=load("nws_alerts.json")))
        ctx = SourceContext("weather_alerts", store, lambda: WeatherAlertsConfig(), http)
        ctx.location = (42.8864, -78.8784)
        ctx.sleep = sleep
        with pytest.raises(asyncio.CancelledError):
            await src.run(ctx)
    assert 120 < naps[0] <= 125 < WeatherAlertsConfig().poll_seconds
    assert [a["event"] for a in store.get().get("weather.alerts")] == ["Extreme Heat Warning", "Heat Advisory"]
    assert naps[1] == WeatherAlertsConfig().poll_seconds - naps[0]              # the rest of the interval, then a fetch
    assert fetch_in == [WeatherAlertsConfig().poll_seconds, naps[1]]            # diagnostics still show the fetch, not the nap
    src._raw = []
    assert src._seconds_to_next_expiry(WeatherAlertsConfig()) == math.inf


@pytest.mark.asyncio
async def test_a_malformed_payload_is_a_failed_poll_not_a_crash():
    src = WeatherAlertsSource(clock=lambda: NOW)
    store = SnapshotStore()
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r"https://api\.weather\.gov/alerts/active.*").mock(return_value=httpx.Response(200, json=load("nws_alerts.json")))
        ctx = SourceContext("weather_alerts", store, lambda: WeatherAlertsConfig(), http)
        ctx.location = (42.8864, -78.8784)
        await src.poll(ctx, WeatherAlertsConfig(), ctx.location)
        version = store.get().version
        for body in (["not", "a", "collection"], {"features": ["x"]}, {"features": [{"properties": {"event": 7, "status": "Actual"}}]}):
            route.mock(return_value=httpx.Response(200, json=body))
            await src.poll(ctx, WeatherAlertsConfig(), ctx.location)        # logged and re-filtered, not raised
        assert store.get().version == version and len(store.get().get("weather.alerts")) == 4
    with pytest.raises(ValueError):
        parse_nws({"features": ["x"]})


def test_out_of_bounds_is_a_value_error():
    assert issubclass(OutOfBounds, ValueError)
    assert "point=" in NWS_ALERTS and "weather-alerts" in ECCC_ALERTS


# -- boards -----------------------------------------------------------------------


def _ctx(alerts, w=128, h=64, elapsed=2.0, event=None, now=None):
    snap = SnapshotStore().publish("weather.alerts", alerts)
    return BoardContext(snapshot=snap, profile=profile_for(w, h), width=w, height=h, fps=30,
                        now=now or datetime(2026, 9, 8, 16, 20, tzinfo=TORONTO), elapsed=elapsed, event=event)


def test_until_text_is_local_and_drops_the_day_when_it_is_today():
    now = datetime(2026, 9, 8, 16, 20, tzinfo=TORONTO)
    assert until_text("2026-09-08T16:45:00-04:00", now) == "UNTIL 4:45 PM"
    assert until_text("2026-09-09T15:00:00-07:00", now) == "UNTIL WED 6:00 PM"      # converted into the panel's zone
    assert until_text("2026-09-10T11:02:00.000Z", now) == "UNTIL THU 7:02 AM"
    assert until_text(None, now) == "" and until_text("garbage", now) == ""


def test_alerts_board_cycles_and_renders_every_size():
    alerts = [alert(), alert("Winter Storm Watch", "Niagara, NY")]
    board, cfg = AlertsBoard(), AlertsBoardConfig(seconds_per_alert=5)
    for w, h in [(128, 64), (64, 32), (128, 32)]:
        img = board.render(_ctx(alerts, w, h), cfg)
        assert img.size == (w, h) and img.getbbox() is not None
    first = board.render(_ctx(alerts, elapsed=1.0), cfg)
    second = board.render(_ctx(alerts, elapsed=6.0), cfg)
    r, g, b = first.getpixel((64, 3))                                   # red bar, mid-breath
    assert r > 100 and g < r // 4 and b < r // 4
    assert second.getpixel((64, 3)) == LEVEL_COLORS["watch"]           # orange bar, static
    ctx = _ctx(alerts, elapsed=9.9)
    assert not board.done(ctx, cfg) and board.done(_ctx(alerts, elapsed=10.0), cfg)
    assert board.auto_seconds(ctx, cfg) == 10.0
    assert AlertsBoard.requires == frozenset({"weather.alerts"})
    lapsed = [alert(), alert("Winter Storm Watch", "Niagara, NY", expires="2026-09-08T15:00:00-04:00")]
    assert board.auto_seconds(_ctx(lapsed), cfg) == 5.0 and board.done(_ctx(lapsed, elapsed=5.0), cfg)
    assert board.auto_seconds(_ctx([]), cfg) == 0.0


def test_alerts_board_follows_the_snapshot_without_being_re_entered():
    """The director restarts a board from elapsed 0 on a state change without calling enter,
    so an alert retired by the last poll must not come back."""
    board, cfg = AlertsBoard(), AlertsBoardConfig(seconds_per_alert=5)
    both = [alert(), alert("Winter Storm Watch", "Niagara, NY")]
    board.enter(_ctx(both, elapsed=0.0), cfg)
    board.render(_ctx(both, elapsed=1.0), cfg)
    only = [alert("Winter Storm Watch", "Niagara, NY")]
    assert board.render(_ctx(only, elapsed=1.0), cfg).getpixel((64, 3)) == LEVEL_COLORS["watch"]
    assert board.done(_ctx(only, elapsed=5.0), cfg) and not board.done(_ctx(both, elapsed=5.0), cfg)


def test_alerts_board_with_nothing_to_show_says_so_and_hands_back_the_screen():
    board = AlertsBoard()
    img = board.render(_ctx([]), AlertsBoardConfig())
    assert img.getbbox() is not None and board.done(_ctx([], elapsed=0.1), AlertsBoardConfig())
    lapsed = _ctx([alert(expires="2026-09-08T15:00:00-04:00")], elapsed=0.1)   # ended before ctx.now (16:20)
    board.render(lapsed, AlertsBoardConfig())
    assert board.done(lapsed, AlertsBoardConfig())


def test_alert_board_matches_by_level_and_live_game():
    board = AlertBoard()
    warning = Event("weather.alert", payload={"alert": alert(), "live_game": False})
    watch = Event("weather.alert", payload={"alert": alert("Winter Storm Watch"), "live_game": False})
    live = Event("weather.alert", payload={"alert": alert(), "live_game": True})
    assert board.matches(warning, AlertBoardConfig()) and not board.matches(watch, AlertBoardConfig())
    assert board.matches(watch, AlertBoardConfig(min_level="watch"))
    assert board.matches(live, AlertBoardConfig()) and not board.matches(live, AlertBoardConfig(interrupt_live_game=False))
    assert not board.matches(warning, AlertBoardConfig(enabled=False))
    assert not board.matches(Event("flights.overhead"), AlertBoardConfig())


def test_alert_board_flashes_then_holds_the_card():
    board, cfg = AlertBoard(), AlertBoardConfig(duration=4)
    event = Event("weather.alert", payload={"alert": alert(), "live_game": False})
    ctx = _ctx([alert()], elapsed=0.0, event=event)
    board.enter(ctx, cfg)
    assert 4.5 <= board.auto_seconds(ctx, cfg) <= 5.5
    held = board.render(_ctx([alert()], elapsed=2.0, event=event), cfg)
    assert held.getbbox() is not None and not board.done(_ctx([], elapsed=2.0, event=event), cfg)
    assert board.done(_ctx([], elapsed=6.0, event=event), cfg)
    small = AlertBoard()
    small.enter(_ctx([alert()], 64, 32, elapsed=0.0, event=event), cfg)
    assert small.render(_ctx([alert()], 64, 32, elapsed=2.0, event=event), cfg).size == (64, 32)


def test_alert_board_builds_the_card_once(monkeypatch):
    import scoreboard.extras.weather.alerts.board as mod
    calls = []
    real = mod.card
    monkeypatch.setattr(mod, "card", lambda *a, **k: calls.append(1) or real(*a, **k))
    event = Event("weather.alert", payload={"alert": alert(), "live_game": False})
    AlertBoard().enter(_ctx([alert()], elapsed=0.0, event=event), AlertBoardConfig(duration=4))
    assert len(calls) == 1


def test_plugins_register_the_source_boards_and_detector():
    reg = load_registry()
    assert {"weather.alerts", "weather.alert"} <= set(reg.boards) and "weather_alerts" in reg.sources
    assert detect_alerts in reg.detectors
