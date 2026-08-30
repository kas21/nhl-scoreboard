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


def client(tmp_path):
    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard())})
    director = Director(config, snapshots, reg, events)
    return TestClient(create_app(config, snapshots, reg, director, PreviewHub())), config


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


def test_override_and_system_endpoints(tmp_path):
    from scoreboard.boards.test_pattern import TestPatternBoard
    from scoreboard.web.api import SystemControl
    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard(), TestPatternBoard())})
    director = Director(config, snapshots, reg, events)
    restarted = []
    c = TestClient(create_app(config, snapshots, reg, director, PreviewHub(), system=SystemControl(lambda: restarted.append(1))))
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
    c = TestClient(create_app(config, snapshots, reg, director, PreviewHub(), health=health))
    rows = c.get("/api/sources").json()
    assert [r["key"] for r in rows] == ["nhl"]
    row = rows[0]
    assert row["status"] == "ok" and row["running"] is True
    assert row["fetches"] == 1 and row["publishes"] == 1 and row["keys"] == ["nhl.scores"]
    assert "last_ok_ago" in row and "next_poll_in" in row


def test_sources_endpoint_without_health_is_empty(tmp_path):
    c, _ = client(tmp_path)
    assert c.get("/api/sources").json() == []
