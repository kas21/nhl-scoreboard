from scoreboard.data import Event, SnapshotStore
from scoreboard.data.events import EventBus


def test_publish_creates_new_versioned_snapshot():
    store = SnapshotStore()
    s0 = store.get()
    s1 = store.publish("weather", {"temp": 3})
    assert s0.version == 0 and s1.version == 1
    assert s0.get("weather") is None
    assert s1.get("weather") == {"temp": 3}
    assert s1.age("weather") is not None and s1.age("weather") < 1


def test_detectors_run_on_change_and_queue_events():
    store, bus = SnapshotStore(), EventBus()
    store.subscribe(bus.on_snapshot)

    def score_detector(prev, new):
        a, b = (prev.get("game") or {}).get("score", 0), (new.get("game") or {}).get("score", 0)
        return [Event("goal", payload={"score": b})] if b > a else []

    bus.register(score_detector)
    store.publish("game", {"score": 0})
    store.publish("game", {"score": 1})
    events = bus.drain()
    assert [e.kind for e in events] == ["goal"]
    assert bus.drain() == ()


def test_drain_collapses_bursts_to_latest_per_kind_and_team():
    bus = EventBus()
    bus._queue = [Event("goal", team="TOR", payload={"n": 1}), Event("goal", team="TOR", payload={"n": 2}),
                  Event("goal", team="MTL"), Event("penalty", team="TOR")]
    events = bus.drain()
    assert [(e.kind, e.team) for e in events] == [("goal", "TOR"), ("goal", "MTL"), ("penalty", "TOR")]
    assert events[0].payload == {"n": 2}
