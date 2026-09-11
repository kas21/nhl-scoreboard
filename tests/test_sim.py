"""The simulator: snapshot claims, the hub, the NHL engine and the API.

The engine is exercised through the hub with a fake clock, and the real NHL detector is
wired in, because what matters is not the engine's bookkeeping but that a simulated goal
produces exactly the ``nhl.goal`` event a real one does — that is what the boards react to.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scoreboard.boards.clock import ClockBoard
from scoreboard.boards.splash import SplashBoard
from scoreboard.config import ConfigStore
from scoreboard.data import SnapshotStore
from scoreboard.data.events import EventBus
from scoreboard.data.store import ClaimError
from scoreboard.director import Director
from scoreboard.nhl.boards.events import GoalBoard, PenaltyBoard
from scoreboard.nhl.boards.game import GameBoard
from scoreboard.nhl.events import detect_main_event
from scoreboard.nhl.sim import INTERMISSION_SECONDS, SIM_GAME_ID, NhlSim
from scoreboard.output import PreviewHub
from scoreboard.plugins import Registry
from scoreboard.sim import SimError, SimulatorHub
from scoreboard.web.api import create_app
from scoreboard.web.guard import UI_HEADER, UI_TOKEN

UI = {"headers": {UI_HEADER: UI_TOKEN}, "base_url": "http://localhost"}


# -- snapshot claims --------------------------------------------------------------

def test_claimed_key_shadows_other_publishers_and_restores_on_release():
    store = SnapshotStore()
    seen = []
    store.subscribe(lambda p, n: seen.append(n.version))
    store.publish("nhl.main_event", {"real": 1}, owner="nhl")
    store.claim("nhl.main_event", owner="sim:nhl")
    store.publish("nhl.main_event", {"fake": 1}, owner="sim:nhl")
    assert store.get().get("nhl.main_event") == {"fake": 1}
    before = len(seen)
    store.publish("nhl.main_event", {"real": 2}, owner="nhl")         # shadowed: no change, no listener
    assert store.get().get("nhl.main_event") == {"fake": 1} and len(seen) == before
    assert store.claims() == {"nhl.main_event": "sim:nhl"}
    store.release("nhl.main_event", owner="sim:nhl")
    assert store.get().get("nhl.main_event") == {"real": 2}           # the freshest real value, not the stale one
    assert len(seen) == before + 1 and store.claims() == {}


def test_release_restores_the_pre_claim_value_when_nothing_arrived_meanwhile():
    store = SnapshotStore()
    store.publish("k", "real", owner="src")
    store.claim(["k"], owner="a")
    store.publish("k", "fake", owner="a")
    store.release(["k"], owner="a")
    assert store.get().get("k") == "real"


def test_claim_is_all_or_nothing_across_owners():
    store = SnapshotStore()
    store.claim(["a", "b"], owner="one")
    with pytest.raises(ClaimError):
        store.claim(["c", "b"], owner="two")
    assert store.claims() == {"a": "one", "b": "one"}                 # "c" was not taken either
    store.claim(["a"], owner="one")                                    # re-claiming your own is fine
    store.release(["a", "b", "zzz"], owner="two")                      # not yours: ignored
    assert store.claims() == {"a": "one", "b": "one"}


# -- hub + engine -------------------------------------------------------------------

class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def rig(tmp_path):
    config = ConfigStore(tmp_path / "config.json")
    store, bus, clock = SnapshotStore(), EventBus(), Clock()
    store.subscribe(bus.on_snapshot)
    bus.register(detect_main_event)
    hub = SimulatorHub(store, config.get, {"nhl": NhlSim()}, clock=clock)
    return hub, store, bus, clock, config


def main(store):
    return store.get().get("nhl.main_event")


def kinds(bus):
    return [(e.kind, e.team) for e in bus.drain()]


def test_start_publishes_a_pregame_game_in_the_feed_shape(rig):
    hub, store, bus, clock, config = rig
    config.update({"sources": {"nhl": {"favorites": ["BOS", "TOR"]}}})
    store.publish("nhl.scores", [{"id": 1, "away": {"abbrev": "X"}, "home": {"abbrev": "Y"}}], owner="nhl")
    store.publish("nhl.standings", {"teams": {"TOR": {"wins": 40, "losses": 20, "otl": 5}}}, owner="nhl")
    hub.start("nhl", {"away": "MTL", "home": "TOR"})
    g = main(store)
    assert g["phase"] == "pregame" and g["state"] == "FUT" and g["sport"] == "nhl" and g["id"] == SIM_GAME_ID
    assert g["favorite_side"] == "home" and g["home"]["record"] == "40-20-5" and g["away"]["record"] == ""
    assert g["home"]["name"] == "Maple Leafs" and g["start_time_utc"].endswith("Z") and g["date"]
    assert g["powerplay"] == {"code": "ev", "clock": ""} and g["goals"] == [] and g["penalties"] == []
    assert [x["id"] for x in store.get().get("nhl.scores")] == [SIM_GAME_ID, 1]      # on top of the real slate
    assert store.claims() == {"nhl.main_event": "sim:nhl", "nhl.scores": "sim:nhl"}
    assert hub.running() == ["nhl"] and hub.state()["active"]
    assert kinds(bus) == []                                                          # nothing to celebrate yet


def test_favorite_auto_follows_priority_order_and_can_be_forced(rig):
    hub, store, bus, clock, config = rig
    config.update({"sources": {"nhl": {"favorites": ["MTL", "TOR"]}}})
    hub.start("nhl", {"away": "MTL", "home": "TOR"})
    assert main(store)["favorite_side"] == "away"
    hub.start("nhl", {"away": "MTL", "home": "TOR", "favorite": "none"})
    assert main(store)["favorite_side"] is None
    hub.start("nhl", {"away": "BOS", "home": "DET"})
    assert main(store)["favorite_side"] is None


def test_clock_runs_at_speed_and_only_publishes_when_something_changed(rig):
    hub, store, bus, clock, config = rig
    hub.start("nhl", {"away": "MTL", "home": "TOR", "start_in": "live", "speed": 10})
    g = main(store)
    assert g["phase"] == "live" and g["period"] == "1st" and g["clock"] == "20:00" and g["clock_running"]
    v = store.get().version
    clock.t += 0.05
    hub.tick()
    assert store.get().version == v                        # half a game-second: the clock string did not move
    clock.t += 6
    hub.tick()
    assert main(store)["clock"] == "19:00"
    hub.action("nhl", "clock")                            # stop
    assert not main(store)["clock_running"]
    clock.t += 60
    hub.tick()
    assert main(store)["clock"] == "19:00"
    hub.action("nhl", "set_clock", {"clock": "01:30"})
    assert main(store)["clock"] == "01:30"
    with pytest.raises(SimError):
        hub.action("nhl", "set_clock", {"clock": "nope"})


def test_goals_fire_the_real_detector_with_scorer_and_assists(rig):
    hub, store, bus, clock, config = rig
    hub.start("nhl", {"away": "MTL", "home": "TOR", "start_in": "live"})
    bus.drain()
    hub.action("nhl", "goal", {"side": "home", "scorer": "Auston Matthews", "assists": "Marner, Nylander"})
    events = bus.drain()
    assert [(e.kind, e.team) for e in events] == [("nhl.goal", "TOR")]
    goal = events[0].payload["goal"]
    assert goal["scorer"] == "Auston Matthews" and goal["last_name"] == "Matthews" and goal["assists"] == ["Marner", "Nylander"]
    assert events[0].payload["score"] == "0-1" and events[0].payload["game"]["favorite_side"] == "home"
    hub.action("nhl", "goal", {"side": "away"})           # a stand-in scorer gets a name and a number
    g = main(store)
    assert g["away"]["score"] == 1 and g["goals"][-1]["scorer"] and g["goals"][-1]["sweater"]
    assert g["goals"][-1]["time"] == "00:00" and g["goals"][-1]["period"] == 1
    hub.action("nhl", "overturn", {"side": "away"})
    assert main(store)["away"]["score"] == 0 and kinds(bus)[-1] == ("nhl.goal_overturned", "MTL")


def test_penalty_gives_a_timed_power_play_that_a_goal_ends_early(rig):
    hub, store, bus, clock, config = rig
    hub.start("nhl", {"away": "MTL", "home": "TOR", "start_in": "live"})
    bus.drain()
    hub.action("nhl", "penalty", {"side": "away", "minutes": "2", "kind": "hooking", "player": "Nick Suzuki"})
    assert ("nhl.penalty", "MTL") in kinds(bus)
    g = main(store)
    assert g["powerplay"] == {"code": "h54", "clock": "02:00"}
    assert g["penalties"][-1] == {"team": "MTL", "period": 1, "time": "00:00", "type": "MIN", "duration": 2, "desc": "hooking", "player": "Nick Suzuki"}
    clock.t += 30
    hub.tick()
    assert main(store)["powerplay"]["clock"] == "01:30"
    hub.action("nhl", "goal", {"side": "home"})
    g = main(store)
    assert g["goals"][-1]["strength"] == "pp" and g["powerplay"]["code"] == "ev"
    assert len(g["penalties"]) == 1                          # the log keeps it; only the power play ended


def test_penalties_expire_stack_to_five_on_three_and_a_pulled_goalie_adds_a_skater(rig):
    hub, store, bus, clock, config = rig
    hub.start("nhl", {"away": "MTL", "home": "TOR", "start_in": "live"})
    for _ in range(3):
        hub.action("nhl", "penalty", {"side": "home", "minutes": "2"})
    assert main(store)["powerplay"]["code"] == "a53"         # two men is the floor
    hub.action("nhl", "pull_away")
    g = main(store)
    assert g["pulled_goalie"] == 1 and g["powerplay"]["code"] == "a63"
    hub.action("nhl", "pull_away")
    clock.t += 121
    hub.tick()
    assert main(store)["powerplay"]["code"] == "ev" and main(store)["pulled_goalie"] == 0
    hub.action("nhl", "penalty", {"side": "home", "minutes": "5", "kind": "fighting"})
    hub.action("nhl", "goal", {"side": "away"})              # a major does not end on a goal
    assert main(store)["powerplay"]["code"] == "a54"
    hub.action("nhl", "clear_penalties")
    assert main(store)["powerplay"]["code"] == "ev"


def test_period_end_intermission_and_next_period(rig):
    hub, store, bus, clock, config = rig
    hub.start("nhl", {"away": "MTL", "home": "TOR", "start_in": "live", "period_minutes": 1})
    clock.t += 61
    hub.tick()
    g = main(store)
    assert g["phase"] == "intermission" and g["in_intermission"] and g["period"] == "1st" and g["clock"] == "18:00"
    assert g["clock_running"]                                # the intermission counts itself down
    with pytest.raises(SimError):
        hub.action("nhl", "goal", {"side": "home"})
    names = [a["name"] for a in hub.state()["sims"][0]["actions"]]
    assert names[0] == "next_period" and "goal" not in names
    hub.action("nhl", "next_period")
    g = main(store)
    assert g["phase"] == "live" and g["period"] == "2nd" and g["clock"] == "01:00" and not g["clock_running"]
    hub.action("nhl", "end_period")
    clock.t += INTERMISSION_SECONDS + 1
    hub.tick()                                               # ran out on its own
    assert main(store)["period"] == "3rd" and main(store)["phase"] == "live"


def test_regular_season_tie_goes_to_overtime_then_shootout(rig):
    hub, store, bus, clock, config = rig
    hub.start("nhl", {"away": "MTL", "home": "TOR", "start_in": "live", "period_minutes": 1})
    for _ in range(3):
        hub.action("nhl", "end_period")
        if main(store)["phase"] == "intermission":
            hub.action("nhl", "next_period")
    g = main(store)
    assert g["period"] == "OT" and g["clock"] == "05:00" and g["phase"] == "live"
    hub.action("nhl", "end_period")
    hub.action("nhl", "next_period")
    g = main(store)
    assert g["period"] == "SO" and g["clock"] == "00:00"
    assert hub.state()["sims"][0]["actions"][0]["name"] == "goal"
    bus.drain()
    hub.action("nhl", "goal", {"side": "away"})
    g = main(store)
    assert g["phase"] == "postgame" and g["outcome"] == "FINAL/SO" and g["away"]["score"] == 1
    assert ("nhl.goal", "MTL") in kinds(bus)
    assert [a["name"] for a in hub.state()["sims"][0]["actions"]] == ["reset"]


def test_overtime_goal_ends_the_game_and_playoffs_keep_going(rig):
    hub, store, bus, clock, config = rig
    hub.start("nhl", {"away": "MTL", "home": "TOR", "start_in": "live", "period_minutes": 1, "game_type": "playoff"})
    for _ in range(3):
        hub.action("nhl", "end_period")
        hub.action("nhl", "next_period")
    assert main(store)["period"] == "OT" and main(store)["clock"] == "20:00" and main(store)["type"] == 3
    hub.action("nhl", "end_period")
    hub.action("nhl", "next_period")
    assert main(store)["period"] == "2OT"                    # no shootout in the playoffs
    hub.action("nhl", "goal", {"side": "home"})
    assert main(store)["outcome"] == "FINAL/2OT" and main(store)["phase"] == "postgame"


def test_final_reset_and_stop_restore_the_real_feed(rig):
    hub, store, bus, clock, config = rig
    store.publish("nhl.main_event", {"real": 1}, owner="nhl")
    hub.start("nhl", {"away": "MTL", "home": "TOR", "start_in": "live"})
    hub.action("nhl", "goal", {"side": "home"})
    hub.action("nhl", "final")
    g = main(store)
    assert g["phase"] == "postgame" and g["outcome"] == "FINAL" and g["state"] == "OVER" and not g["clock_running"]
    hub.action("nhl", "reset")
    assert main(store)["phase"] == "pregame" and main(store)["home"]["score"] == 0
    store.publish("nhl.main_event", {"real": 2}, owner="nhl")       # shadowed while the sim runs
    assert main(store)["phase"] == "pregame"
    hub.stop("nhl")
    assert main(store) == {"real": 2} and store.claims() == {} and not hub.active
    with pytest.raises(KeyError):
        hub.action("nhl", "goal")


def test_hub_refuses_bad_options_and_conflicting_claims(rig):
    hub, store, bus, clock, config = rig
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        hub.start("nhl", {"away": "XXX"})
    with pytest.raises(SimError):
        hub.start("nhl", {"away": "TOR", "home": "TOR"})
    assert store.claims() == {}                                      # a failed start holds nothing

    class Other:
        key, title, description = "other", "Other", ""
        options_model = NhlSim.options_model
        claims = frozenset({"nhl.scores"})

        def start(self, o, c): ...
        def tick(self, c): return False
        def action(self, n, p, c): ...
        def actions(self): return []
        def values(self): return {}
        def describe(self): return {"headline": "", "lines": []}

    hub2 = SimulatorHub(store, config.get, {"nhl": NhlSim(), "other": Other()}, clock=clock)
    hub2.start("nhl", {})
    with pytest.raises(ClaimError):
        hub2.start("other", {})
    assert hub2.running() == ["nhl"]
    hub2.stop_all()
    assert store.claims() == {}


def test_a_crashing_engine_is_stopped_not_left_holding_its_claims(rig):
    hub, store, bus, clock, config = rig
    hub.start("nhl", {"away": "MTL", "home": "TOR", "start_in": "live"})
    hub._sims["nhl"].tick = lambda ctx: 1 / 0
    clock.t += 5
    hub.tick()
    assert not hub.active and store.claims() == {}


# -- through the director ------------------------------------------------------------

def test_simulated_goal_interrupts_the_live_board(rig, tmp_path):
    hub, store, bus, clock, config = rig
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard(), GameBoard(), GoalBoard(), PenaltyBoard())})
    director = Director(config, store, reg, bus)
    t = 1000.0
    director.frame(t)
    hub.start("nhl", {"away": "MTL", "home": "TOR", "start_in": "live"})
    store.publish("main_event", main(store))                          # what the arbiter would do
    t += 5
    director.frame(t)
    assert director.state.value == "live" and director.active_board == "nhl.game"
    hub.action("nhl", "goal", {"side": "home"})
    store.publish("main_event", main(store))
    director.frame(t + 0.1)
    assert director.active_board == "nhl.goal"
    hub.action("nhl", "penalty", {"side": "away"})
    store.publish("main_event", main(store))
    t += 40                                                          # past the goal sequence
    director.frame(t)
    director.frame(t + 0.1)
    assert director.active_board == "nhl.penalty"


# -- API ------------------------------------------------------------------------------

def client(tmp_path):
    config = ConfigStore(tmp_path / "config.json")
    store, bus = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard())}, sims={"nhl": NhlSim()})
    director = Director(config, store, reg, bus)
    hub = SimulatorHub(store, config.get, reg.sims)
    return TestClient(create_app(config, store, reg, director, PreviewHub(), simulator=hub), **UI), store


def test_api_lists_forms_starts_acts_and_stops(tmp_path):
    c, store = client(tmp_path)
    listing = c.get("/api/sim").json()
    assert listing["active"] is False
    sim = listing["sims"][0]
    assert sim["key"] == "nhl" and not sim["running"] and sim["claims"] == ["nhl.main_event", "nhl.scores"]
    assert sim["schema"]["properties"]["home"]["default"] == "TOR"
    assert sim["schema"]["properties"]["home"]["labels"]["TOR"].startswith("TOR")
    assert c.post("/api/sim/nope/start", json={}).status_code == 404
    assert c.post("/api/sim/nhl/start", json={"away": "ZZZ"}).status_code == 422
    assert c.post("/api/sim/nhl/start", json={"away": "TOR", "home": "TOR"}).status_code == 422
    r = c.post("/api/sim/nhl/start", json={"away": "BOS", "home": "TOR", "start_in": "live"})
    assert r.status_code == 200 and r.json()["active"]
    running = r.json()["sims"][0]
    assert running["running"] and running["options"]["away"] == "BOS" and running["state"]["headline"] == "BOS 0 - 0 TOR"
    assert any(a["name"] == "goal" and a["primary"] for a in running["actions"])
    assert c.get("/api/status").json()["simulating"] == ["nhl"]
    assert c.post("/api/sim/nhl/action", json={"name": "goal", "params": {"side": "home"}}).json()["sims"][0]["state"]["headline"] == "BOS 0 - 1 TOR"
    assert c.post("/api/sim/nhl/action", json={"name": "next_period", "params": {}}).status_code == 422
    assert c.post("/api/sim/nhl/action", json={"params": {}}).status_code == 422
    assert store.get().get("nhl.main_event")["home"]["score"] == 1
    assert c.post("/api/sim/nhl/stop").json()["active"] is False
    assert c.post("/api/sim/nhl/action", json={"name": "goal", "params": {}}).status_code == 409
    c.post("/api/sim/nhl/start", json={})
    assert c.post("/api/sim/stop").json()["active"] is False and store.claims() == {}
    # state-changing calls still need the UI header
    plain = TestClient(c.app, base_url="http://localhost")
    assert plain.post("/api/sim/nhl/start", json={}).status_code == 403
