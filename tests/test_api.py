from datetime import UTC

from fastapi.testclient import TestClient

from scoreboard.boards.clock import ClockBoard
from scoreboard.boards.splash import SplashBoard
from scoreboard.config import ConfigStore
from scoreboard.data import SnapshotStore
from scoreboard.data.events import EventBus
from scoreboard.director import Director
from scoreboard.output import PreviewHub
from scoreboard.plugins import Registry
from scoreboard.web.api import create_app
from scoreboard.web.guard import UI_HEADER, UI_TOKEN

# The bundled UI is served same-origin and tags its requests; see web/guard.py. Tests
# stand in for that UI, so they connect the way it does.
UI = {"headers": {UI_HEADER: UI_TOKEN}, "base_url": "http://localhost"}


def client(tmp_path):
    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard())})
    director = Director(config, snapshots, reg, events)
    return TestClient(create_app(config, snapshots, reg, director, PreviewHub()), **UI), config


def test_status_and_config_roundtrip(tmp_path):
    c, config = client(tmp_path)
    assert c.get("/api/status").json()["state"] == "boot"
    r = c.patch("/api/config", json={"brightness": {"day": 33}})
    assert r.status_code == 200 and r.json()["brightness"]["day"] == 33
    assert config.get().brightness.day == 33


def test_invalid_patch_is_422(tmp_path):
    c, _ = client(tmp_path)
    assert c.patch("/api/config", json={"brightness": {"day": 0}}).status_code == 422


def test_schema_includes_board_models(tmp_path):
    c, _ = client(tmp_path)
    schema = c.get("/api/schema").json()
    assert "clock" in schema["properties"]["boards"]["properties"]
    assert "format" in schema["properties"]["boards"]["properties"]["clock"]["properties"]


def test_schema_marks_expert_fields_advanced(tmp_path):
    """The web UI hides `advanced` fields behind a toggle, so the hint must survive export."""
    c, _ = client(tmp_path)
    schema = c.get("/api/schema").json()
    display = schema["$defs"]["DisplayConfig"]["properties"]
    assert display["pwm_lsb_nanoseconds"]["advanced"] is True
    assert "advanced" not in display["width"]                       # everyday fields stay visible
    assert schema["$defs"]["WebConfig"]["properties"]["host"]["advanced"] is True
    assert "advanced" not in schema["$defs"]["WebConfig"]["properties"]["port"]


def test_boards_and_index(tmp_path):
    c, _ = client(tmp_path)
    keys = {b["key"] for b in c.get("/api/boards").json()}
    assert keys == {"clock", "splash"}
    assert c.get("/").status_code == 200
    assert c.get("/api/preview.png").status_code == 404


def test_boards_report_what_auto_duration_means(tmp_path):
    """A blank playlist duration reads as "auto"; the UI needs a number to put beside it."""
    from scoreboard.extras.weather.alerts.board import AlertBoard
    from scoreboard.extras.weather.board import WeatherBoard

    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), WeatherBoard(), AlertBoard())})
    director = Director(config, snapshots, reg, events)
    c = TestClient(create_app(config, snapshots, reg, director, PreviewHub()), **UI)

    by_key = {b["key"]: b for b in c.get("/api/boards").json()}
    assert by_key["clock"] == {**by_key["clock"], "self_timed": False, "auto_seconds": None}
    assert by_key["weather.current"]["self_timed"] is True
    assert by_key["weather.current"]["playlistable"] is True and by_key["weather.alert"]["playlistable"] is False
    assert by_key["weather.current"]["auto_seconds"] == 15.0        # WeatherBoardConfig.duration default
    c.patch("/api/config", json={"boards": {"weather.current": {"duration": 12}}})
    assert {b["key"]: b["auto_seconds"] for b in c.get("/api/boards").json()}["weather.current"] == 12.0


def test_override_and_system_endpoints(tmp_path):
    from scoreboard.boards.test_pattern import TestPatternBoard
    from scoreboard.web.api import SystemControl
    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard(), TestPatternBoard())})
    director = Director(config, snapshots, reg, events)
    restarted = []
    c = TestClient(create_app(config, snapshots, reg, director, PreviewHub(), system=SystemControl(lambda: restarted.append(1))), **UI)
    assert c.post("/api/override", json={"board": "nope"}).status_code == 404
    assert c.post("/api/override", json={"board": "test_pattern", "seconds": 30}).json() == {"override": "test_pattern"}
    director.frame(1000.0)
    director.frame(1000.0 + 10)
    assert director.active_board == "test_pattern"          # overrides even the boot splash
    assert c.post("/api/override", json={"board": None}).json() == {"override": None}
    info = c.get("/api/system").json()
    assert info["can_restart"] and info["hostname"]
    assert c.post("/api/system/restart").json() == {"restarting": True}
    assert restarted == [1]
    assert c.post("/api/system/hostname", json={"hostname": "Bad Name!"}).status_code == 422


def test_test_pattern_board_renders():
    from datetime import datetime

    from scoreboard.boards.base import BoardContext
    from scoreboard.boards.test_pattern import TestPatternBoard
    from scoreboard.data import Snapshot
    from scoreboard.render.profiles import profile_for
    ctx = BoardContext(snapshot=Snapshot(), profile=profile_for(128, 64), width=128, height=64, fps=30,
                       now=datetime(2026, 1, 1, tzinfo=UTC), elapsed=0.0)
    img = TestPatternBoard().render(ctx, None)
    assert img.getpixel((20, 60)) == (255, 0, 0) and img.getpixel((120, 60)) == (255, 255, 255)


def test_geocode_proxy(tmp_path):
    import httpx
    import respx
    c, _ = client(tmp_path)
    with respx.mock() as mock:
        mock.get(url__regex=r"https://geocoding-api\.open-meteo\.com/.*").mock(return_value=httpx.Response(200, json={"results": [
            {"name": "Toronto", "admin1": "Ontario", "country_code": "CA", "latitude": 43.70011, "longitude": -79.4163, "timezone": "America/Toronto"}]}))
        r = c.get("/api/geocode", params={"q": "Toronto"}).json()
    assert r == [{"name": "Toronto", "region": "Ontario", "country": "CA", "latitude": 43.7, "longitude": -79.416, "timezone": "America/Toronto"}]
    assert c.get("/api/geocode", params={"q": "T"}).json() == []


def test_config_api_returns_effective_plugin_defaults(tmp_path):
    c, config = client(tmp_path)
    cfg = c.get("/api/config").json()
    assert cfg["boards"]["clock"]["format"] == "12h"            # default, not stored
    assert cfg["boards"]["clock"]["show_date"] is True
    assert "clock" not in config.get().boards                   # config.json still holds overrides only
    c.patch("/api/config", json={"boards": {"clock": {"format": "24h"}}})
    assert c.get("/api/config").json()["boards"]["clock"] == {**cfg["boards"]["clock"], "format": "24h"}
    assert config.get().boards["clock"] == {"format": "24h"}


def test_preview_hub_encodes_off_thread_and_drops_when_idle():
    import time

    from PIL import Image

    from scoreboard.output import PreviewHub
    hub = PreviewHub(fps=30)
    frame = Image.new("RGB", (16, 8), (255, 0, 0))
    t0 = time.perf_counter()
    for _ in range(100):
        hub.submit(frame)
    assert (time.perf_counter() - t0) < 0.05          # render-thread cost is a copy, not an encode
    time.sleep(0.05)
    assert hub.latest() is not None and hub.latest()[:4] == b"\x89PNG"


def test_sources_endpoint_reports_health(tmp_path):
    from scoreboard.data.health import SourceHealth

    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard())})
    director = Director(config, snapshots, reg, events)
    health = SourceHealth(clock=lambda: 1000.0)
    health.register("nhl")
    health.set_running("nhl", True)
    health.record_fetch("nhl", ok=True, latency_ms=20.0)
    health.record_publish("nhl", "nhl.scores")
    c = TestClient(create_app(config, snapshots, reg, director, PreviewHub(), health=health), **UI)
    rows = c.get("/api/sources").json()
    assert [r["key"] for r in rows] == ["nhl"]
    row = rows[0]
    assert row["status"] == "ok" and row["running"] is True
    assert row["fetches"] == 1 and row["publishes"] == 1 and row["keys"] == ["nhl.scores"]
    assert "last_ok_ago" in row and "next_poll_in" in row


def test_sources_endpoint_without_health_is_empty(tmp_path):
    c, _ = client(tmp_path)
    assert c.get("/api/sources").json() == []


def test_ui_assets_must_revalidate(tmp_path):
    """The UI is a git checkout the OTA updater rewrites under a running browser.

    Without an explicit Cache-Control, heuristic caching serves a stale app.js from
    disk with no revalidation, so an updated Pi renders the previous UI.
    """
    c, _ = client(tmp_path)
    for path in ("/", "/static/app.js"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == "no-cache", path
        assert r.headers.get("etag"), f"{path} still needs an ETag to make 304s cheap"


def test_snapshot_since_returns_only_what_changed_and_waits_for_it(tmp_path):
    """A follower panel long-polls this: only the keys past its version, held until there are some."""
    import threading
    import time

    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(),)})
    c = TestClient(create_app(config, snapshots, reg, Director(config, snapshots, reg, events), PreviewHub()), **UI)
    snapshots.publish("nhl.scores", [1])
    snapshots.publish("weather.current", {"t": 1})
    full = c.get("/api/snapshot").json()
    assert full["version"] == 2 and set(full["data"]) == {"nhl.scores", "weather.current"}
    assert c.get("/api/snapshot", params={"since": 1}).json() == {"version": 2, "data": {"weather.current": {"t": 1}}}
    assert c.get("/api/snapshot", params={"since": -1}).json()["data"] == full["data"]
    assert c.get("/api/snapshot", params={"since": 50}).json()["data"] == full["data"]     # we restarted; start over
    assert c.get("/api/snapshot", params={"since": 2, "wait": 0}).json() == {"version": 2, "data": {}}

    threading.Timer(0.3, lambda: snapshots.publish("nhl.scores", [2])).start()
    started = time.monotonic()
    body = c.get("/api/snapshot", params={"since": 2, "wait": 5}).json()
    assert body == {"version": 3, "data": {"nhl.scores": [2]}}
    assert time.monotonic() - started < 3, "the wait should end at the publish, not at the timeout"


def test_status_reports_the_link_and_mqtt_state(tmp_path):
    c, config = client(tmp_path)
    st = c.get("/api/status").json()
    assert st["following"] is None and st["mqtt"] is None
    config.update({"follower": {"enabled": True, "master_url": "http://office.local:8080"}})
    assert c.get("/api/status").json()["following"] is None      # the follower source only exists after a restart


def test_rotation_endpoint_is_the_directors_view(tmp_path):
    c, config = client(tmp_path)
    config.update({"playlists": {"offday": [{"board": "clock", "duration": 8}, {"board": "nope", "duration": 3}]}})
    rot = c.get("/api/rotation").json()
    assert rot["state"] == "boot" and rot["entries"] == [] and rot["index"] is None
    assert set(rot) == {"state", "board", "entries", "index", "lap_seconds", "event", "override"}


def test_boards_endpoint_names_the_pace_unit_and_items(tmp_path):
    from scoreboard.nhl.boards.ticker import TickerBoard
    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard(), TickerBoard())})
    c = TestClient(create_app(config, snapshots, reg, Director(config, snapshots, reg, events), PreviewHub()), **UI)
    by_key = {b["key"]: b for b in c.get("/api/boards").json()}
    assert by_key["clock"]["pace_unit"] is None and by_key["clock"]["items"] is None
    assert by_key["nhl.ticker"]["pace_unit"] == "game" and by_key["nhl.ticker"]["items"] == [0, "game"]


def test_plugin_sections_are_validated_on_save(tmp_path):
    """A bad plugin value used to save with a 200 and make the plugin fall back to *all* its
    defaults at runtime (the NHL source forgetting its favourites over a cleared interval)."""
    from scoreboard.plugins import load_registry
    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    reg = load_registry()
    c = TestClient(create_app(config, snapshots, reg, Director(config, snapshots, reg, events), PreviewHub()), **UI)
    r = c.patch("/api/config", json={"sources": {"nhl": {"favorites": ["TOR"], "live_interval": None}}})
    assert r.status_code == 422
    assert [e["loc"] for e in r.json()["detail"]] == [["sources", "nhl", "live_interval"]]
    assert "nhl" not in config.get().sources                                    # nothing was written
    assert c.patch("/api/config", json={"boards": {"clock": {"format": "13h"}}}).status_code == 422
    assert c.put("/api/config", json={"boards": {"clock": {"format": "13h"}}}).status_code == 422
    assert c.patch("/api/config", json={"sources": {"nhl": {"favorites": ["TOR"]}}}).status_code == 200
    # A PATCH is judged as merged: the interval below the minimum is refused even though the
    # patch itself is only that one key.
    assert c.patch("/api/config", json={"sources": {"nhl": {"live_interval": 0.1}}}).status_code == 422
    assert config.get().sources["nhl"] == {"favorites": ["TOR"]}
    # A section for a plugin that is not loaded (a sport source in follower mode) is left alone.
    assert c.patch("/api/config", json={"sources": {"not_a_plugin": {"anything": 1}}}).status_code == 200


def test_override_rejects_seconds_that_are_not_a_number(tmp_path):
    c, _ = client(tmp_path)
    assert c.post("/api/override", json={"board": "clock", "seconds": "soon"}).status_code == 422


def test_reset_keeps_the_way_the_box_is_reached(tmp_path):
    c, config = client(tmp_path)
    c.patch("/api/config", json={"web": {"allowed_hosts": ["sign.home"]}, "brightness": {"day": 42}})
    r = c.post("/api/config/reset")
    assert r.status_code == 200 and r.json()["brightness"]["day"] == 80
    assert config.get().web.allowed_hosts == ["sign.home"]


def test_every_process_has_one_boot_id(tmp_path):
    c, _ = client(tmp_path)
    ids = {c.get("/api/status").json()["boot_id"], c.get("/api/system").json()["boot_id"], c.get("/api/system/update").json()["boot_id"]}
    assert len(ids) == 1 and len(ids.pop()) == 32


def test_rollback_route_reports_nothing_to_roll_back_to(tmp_path):
    from scoreboard.web.updater import Updater
    c, _ = client(tmp_path)
    r = c.post("/api/system/update/rollback")
    assert r.status_code == 200 and r.json()["started"] is False
    assert c.get("/api/system/update").json()["previous"] is None
    assert Updater(root=tmp_path, state_dir=tmp_path / "state").state()["previous"] is None


def test_matrix_output_lets_go_of_the_panel_on_close(monkeypatch):
    """The driver resets GPIO in its destructor, which runs when the object is freed; Clear()
    alone left the refresh thread driving the panel."""
    import sys
    import types

    from scoreboard.config.models import DisplayConfig
    from scoreboard.output.matrix import MatrixOutput
    events = []

    class FakeMatrix:
        def __init__(self, options=None): self.brightness = options.brightness
        def CreateFrameCanvas(self): return types.SimpleNamespace(SetImage=lambda img: None)
        def SwapOnVSync(self, c): return c
        def Clear(self): events.append("clear")
        def __del__(self): events.append("freed")

    fake = types.ModuleType("rgbmatrix")
    fake.RGBMatrix, fake.RGBMatrixOptions = FakeMatrix, lambda: types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "rgbmatrix", fake)
    out = MatrixOutput(DisplayConfig(), emulator=False, brightness=50)
    out.close()
    out.close()                                             # idempotent: app.run and the render loop both call it
    assert events == ["clear", "freed"]
