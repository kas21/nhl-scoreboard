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


def test_boards_and_index(tmp_path):
    c, _ = client(tmp_path)
    keys = {b["key"] for b in c.get("/api/boards").json()}
    assert keys == {"clock", "splash"}
    assert c.get("/").status_code == 200
    assert c.get("/api/preview.png").status_code == 404
