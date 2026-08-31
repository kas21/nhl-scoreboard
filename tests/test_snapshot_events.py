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


def test_drain_loses_nothing_to_the_thread_it_races():
    """Sources publish on the asyncio thread; the director drains on the render thread.

    Without a lock, `drain()` iterates the queue and then rebinds it, so anything the
    detectors append in that window is dropped on the floor — and what gets dropped is
    the goal animation, which is the reason the panel exists. Measured loss under
    contention was on the order of 1 in 10,000 — the kind of bug that never reproduces
    on a bench and eats one goal a season in the living room.
    """
    import threading

    from scoreboard.data.store import Snapshot

    bus = EventBus()
    bus.register(lambda prev, new: [Event("goal", team=str(new.version))])   # unique: nothing collapses
    total = 100_000
    done = threading.Event()
    drained: list[Event] = []

    def publish_side() -> None:
        for i in range(total):
            bus.on_snapshot(Snapshot(i), Snapshot(i + 1))
        done.set()

    def render_side() -> None:
        while not done.is_set():
            drained.extend(bus.drain())
        drained.extend(bus.drain())

    threads = [threading.Thread(target=publish_side), threading.Thread(target=render_side)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(drained) == total
