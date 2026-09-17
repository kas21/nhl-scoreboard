"""The MQTT bridge, against a fake client: what goes out, what a command does, when it reconnects."""
import asyncio
import contextlib
import json

import pytest

from scoreboard import mqtt as mod
from scoreboard.boards.blank import BlankBoard
from scoreboard.boards.clock import ClockBoard
from scoreboard.config import ConfigStore
from scoreboard.config.models import MqttConfig
from scoreboard.data import Event, SnapshotStore
from scoreboard.data.events import EventBus
from scoreboard.director import Director
from scoreboard.mqtt import MqttBridge, parse_board_command, wanted
from scoreboard.plugins import Registry


class FakeMessage:
    def __init__(self, topic, payload):
        self.topic = type("Topic", (), {"value": topic})()
        self.payload = payload


class FakeClient:
    """Enough of aiomqtt.Client: an async context manager with publish / subscribe / messages."""

    def __init__(self, cfg, will_topic, log):
        self.cfg, self.will_topic, self.log = cfg, will_topic, log
        self.published = []
        self.subscribed = []
        self.inbox = asyncio.Queue()
        self.fail_connect = False

    async def __aenter__(self):
        if self.fail_connect:
            raise ConnectionRefusedError("no broker")
        self.log.append(("connect", self.cfg.host))
        return self

    async def __aexit__(self, *exc):
        self.log.append(("disconnect", self.cfg.host))

    async def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, retain))

    async def subscribe(self, topic):
        self.subscribed.append(topic)

    @property
    async def messages(self):
        while True:
            yield await self.inbox.get()

    def sent(self, topic):
        return [json.loads(p) if p not in ("online", "offline") else p for t, p, _ in self.published if t == topic]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "IDLE_RECHECK_SECONDS", 0.01)
    monkeypatch.setattr(mod, "STATE_INTERVAL", 0.02)
    monkeypatch.setattr(mod, "RECONNECT_DELAYS", (0.01,))
    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    snapshots.subscribe(events.on_snapshot)
    reg = Registry(boards={b.key: b for b in (ClockBoard(), BlankBoard())})
    director = Director(config, snapshots, reg, events)
    clients, log = [], []

    def factory(cfg, will_topic):
        c = FakeClient(cfg, will_topic, log)
        clients.append(c)
        return c

    bridge = MqttBridge(lambda: config.get().mqtt, snapshots, events, director, client_factory=factory)
    return config, snapshots, events, director, bridge, clients, log


async def settle(ticks=10):
    for _ in range(ticks):
        await asyncio.sleep(0.01)


@contextlib.asynccontextmanager
async def running(bridge):
    task = asyncio.create_task(bridge.run())
    try:
        yield task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_disabled_bridge_never_connects(world):
    config, snapshots, events, director, bridge, clients, log = world
    async with running(bridge):
        snapshots.publish("nhl.scores", [1])
        await settle()
    assert clients == [] and bridge.status()["enabled"] is False


@pytest.mark.asyncio
async def test_connecting_publishes_the_whole_picture_retained_then_changes(world):
    config, snapshots, events, director, bridge, clients, log = world
    snapshots.publish("nhl.scores", [{"id": 1}])
    snapshots.publish("main_event", None)
    config.update({"mqtt": {"enabled": True, "host": "broker.local", "topic_prefix": "sign"}})
    async with running(bridge):
        await settle()
        c = clients[0]
        assert c.will_topic == "sign/status" and c.subscribed == ["sign/cmd/#"]
        assert c.sent("sign/status") == ["online"]
        assert c.sent("sign/snapshot/nhl/scores") == [[{"id": 1}]]
        assert c.sent("sign/snapshot/main_event") == [None]
        assert all(retain for t, _, retain in c.published if t.startswith("sign/snapshot/"))
        assert c.sent("sign/state")[0]["state"] == "boot"
        assert bridge.status() == {**bridge.status(), "connected": True, "error": None}

        snapshots.publish("nhl.scores", [{"id": 2}])
        await settle()
        assert c.sent("sign/snapshot/nhl/scores") == [[{"id": 1}], [{"id": 2}]]
        assert len(c.sent("sign/state")) == 1, "state is republished only when it changes"
    assert log[-1] == ("disconnect", "broker.local")


@pytest.mark.asyncio
async def test_events_go_out_as_they_happen(world):
    config, snapshots, events, director, bridge, clients, log = world
    events.register(lambda prev, new: [Event("nhl.goal", team="TOR", payload={"score": 1}, ts=5.0)] if new.get("g") else [])
    config.update({"mqtt": {"enabled": True, "host": "broker.local"}})
    async with running(bridge):
        await settle()
        snapshots.publish("g", 1)
        await settle()
        c = clients[0]
        assert c.sent("scoreboard/event/nhl/goal") == [{"kind": "nhl.goal", "team": "TOR", "payload": {"score": 1}, "ts": 5.0}]
        assert not [t for t, _, r in c.published if t.startswith("scoreboard/event/") and r], "events are not retained"


@pytest.mark.asyncio
async def test_commands_drive_the_director(world):
    config, snapshots, events, director, bridge, clients, log = world
    config.update({"mqtt": {"enabled": True, "host": "broker.local"}})
    async with running(bridge):
        await settle()
        c = clients[0]
        await c.inbox.put(FakeMessage("scoreboard/cmd/power", b"off"))
        await settle()
        assert director.override == "blank"
        await c.inbox.put(FakeMessage("scoreboard/cmd/board", b'{"board": "clock", "seconds": 5}'))
        await settle()
        assert director.override == "clock"
        await c.inbox.put(FakeMessage("scoreboard/cmd/board", b""))
        await settle()
        assert director.override is None
        await c.inbox.put(FakeMessage("scoreboard/cmd/board", b"nope"))          # unknown board: nothing forced
        await c.inbox.put(FakeMessage("scoreboard/cmd/board", b"{not json"))     # bad payload: logged, bridge lives on
        await c.inbox.put(FakeMessage("scoreboard/cmd/power", b"on"))
        await settle()
        assert director.override is None and bridge.connected


@pytest.mark.asyncio
async def test_a_settings_change_reconnects_and_a_dead_broker_is_retried(world):
    config, snapshots, events, director, bridge, clients, log = world
    config.update({"mqtt": {"enabled": True, "host": "one.local"}})
    async with running(bridge):
        await settle()
        config.update({"mqtt": {"host": "two.local"}})
        await settle()
        assert [h for kind, h in log if kind == "connect"] == ["one.local", "two.local"]
        assert log == [("connect", "one.local"), ("disconnect", "one.local"), ("connect", "two.local")]
        config.update({"mqtt": {"enabled": False}})
        await settle()
        assert log[-1] == ("disconnect", "two.local") and bridge.connected is False


@pytest.mark.asyncio
async def test_connection_errors_are_reported_and_retried(world, monkeypatch):
    config, snapshots, events, director, bridge, clients, log = world
    attempts = []

    def failing(cfg, will_topic):
        c = FakeClient(cfg, will_topic, log)
        c.fail_connect = len(attempts) < 2
        attempts.append(c)
        return c

    bridge._factory = failing
    config.update({"mqtt": {"enabled": True, "host": "broker.local"}})
    async with running(bridge):
        await settle(20)
        assert len(attempts) >= 3 and bridge.connected and bridge.status()["error"] is None


@pytest.mark.asyncio
async def test_the_error_shows_while_the_broker_is_down(world):
    config, snapshots, events, director, bridge, clients, log = world

    def failing(cfg, will_topic):
        c = FakeClient(cfg, will_topic, log)
        c.fail_connect = True
        return c

    bridge._factory = failing
    config.update({"mqtt": {"enabled": True, "host": "broker.local"}})
    async with running(bridge):
        await settle()
        assert bridge.status()["connected"] is False
        assert "ConnectionRefusedError" in bridge.status()["error"]


def test_snapshot_key_filter():
    assert wanted(MqttConfig(), "flights.nearby")
    only = MqttConfig(snapshot_keys=["nhl", "main_event"])
    assert wanted(only, "nhl.scores") and wanted(only, "main_event")
    assert not wanted(only, "nhl_extra") and not wanted(only, "weather.current")


def test_board_command_payloads():
    assert parse_board_command("") == (None, 0.0)
    assert parse_board_command("none") == (None, 0.0)
    assert parse_board_command("clock") == ("clock", 60.0)
    assert parse_board_command('{"board": "clock", "seconds": 5}') == ("clock", 5.0)
    assert parse_board_command('{"board": null}') == (None, 60.0)


def test_mqtt_config_rejects_a_wildcard_prefix():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        MqttConfig(topic_prefix="sign/#")
    assert MqttConfig(topic_prefix="home/sign").topic_prefix == "home/sign"
