"""The dashboard endpoint: the snapshot trimmed to what the info cards show."""

from fastapi.testclient import TestClient

from scoreboard.boards.clock import ClockBoard
from scoreboard.config import ConfigStore
from scoreboard.data import SnapshotStore
from scoreboard.data.events import EventBus
from scoreboard.director import Director
from scoreboard.output import PreviewHub
from scoreboard.plugins import Registry
from scoreboard.web.api import create_app
from scoreboard.web.dashboard import dashboard_summary, games_by_day
from scoreboard.web.guard import UI_HEADER, UI_TOKEN

TODAY = "2026-04-11"


def game(gid, date, away, home, phase="pregame", start="2026-04-11T23:00:00Z", **extra):
    return {"id": gid, "type": 2, "state": "FUT", "phase": phase, "date": date, "start_time_utc": start,
            "away": {"abbrev": away, "name": away, "score": 0, "record": "1-1-0", "sog": 0},
            "home": {"abbrev": home, "name": home, "score": 0, "record": "2-0-0", "sog": 0},
            "period": "", "clock": "", "outcome": "", "goals": [{"scorer": "x"}], "penalties": [], **extra}


def populated_store() -> SnapshotStore:
    store = SnapshotStore()
    store.publish("nhl.scores", [game(1, TODAY, "MTL", "TOR", phase="live", period="2nd", clock="12:34")])
    store.publish("nhl.schedule", [game(1, TODAY, "MTL", "TOR"), game(2, "2026-04-12", "TOR", "BOS"), game(3, "2026-04-13", "NYR", "NJD")])
    store.publish("nhl.team_summary", {"TOR": {"record": {"wins": 40, "losses": 30, "otl": 5, "points": 85}, "prev_game": None,
                                               "next_game": {"date": "2026-04-12", "opponent": "BOS", "home": False}}})
    store.publish("nhl.season", {"sport": "nhl", "phase": "regular", "days_to_regular": None})
    store.publish("nhl.main_event", {"id": 1, "sport": "nhl"})
    store.publish("main_event", {"id": 1, "sport": "nhl", "favorite_side": "home"})
    store.publish("flights.nearby", [{"hex": "a1", "ident": "ACA123", "callsign": "ACA123", "airline": "Air Canada", "type": "A320",
                                      "altitude_ft": 3000, "distance_km": 2.1, "bearing_compass": "NE", "route": "YYZ-YUL", "lat": 1, "lon": 2},
                                     {"hex": "b2", "ident": "C-GABC", "callsign": "", "altitude_ft": 30000, "distance_km": 20.0, "lat": 1, "lon": 2}])
    store.publish("flights.overhead", [{"hex": "a1"}])
    store.publish("flights.stats", {"airframes": 2, "sightings": 5, "today": 1, "since": 1.0, "regulars": [{"hex": "a1", "count": 4}]})
    store.publish("holidays.upcoming", [{"name": "Easter", "display": "Easter", "date": "2026-04-05", "days": 3, "image": "/x.png", "custom": False}])
    store.publish("weather.current", {"label": "HOME", "temp": 12, "feels": 10, "short": "Cloudy", "icon": "cloud", "units": {"temp": "C"}, "wind": 5, "wind_dir": 90})
    store.publish("weather.daily", [{"date": TODAY, "hi": 15, "lo": 5, "pop": 20, "short": "Cloudy", "icon": "cloud", "sunrise": "x"}] * 6)
    return store


def test_games_by_day_merges_live_slate_over_schedule():
    days = games_by_day([game(1, TODAY, "MTL", "TOR"), game(2, "2026-04-12", "TOR", "BOS")],
                        [game(1, TODAY, "MTL", "TOR", phase="live")])
    assert [d["date"] for d in days] == [TODAY, "2026-04-12"]
    assert days[0]["games"][0]["phase"] == "live"                     # scores win on the same id
    assert len(days[0]["games"]) == 1


def test_summary_trims_games_and_flags_favourite_and_main():
    out = dashboard_summary(populated_store().get(), TODAY)
    assert out["today"] == TODAY and out["main_event"] == {"sport": "nhl", "id": 1}
    nhl = out["sports"][0]
    assert [s["sport"] for s in out["sports"]] == ["nhl"]             # NFL / MLB never published: absent, not empty
    assert nhl["favorites"] == ["TOR"]
    assert [d["date"] for d in nhl["days"]] == [TODAY, "2026-04-12", "2026-04-13"]
    live = nhl["days"][0]["games"][0]
    assert live["main"] and live["favorite"] and live["phase"] == "live" and live["clock"] == "12:34"
    assert "goals" not in live and "sog" not in live["home"]           # trimmed
    assert nhl["days"][1]["games"][0]["favorite"] and not nhl["days"][1]["games"][0]["main"]
    assert not nhl["days"][2]["games"][0]["favorite"]
    assert nhl["teams"]["TOR"]["next_game"]["opponent"] == "BOS" and nhl["teams"]["TOR"]["record"]["points"] == 85
    assert nhl["season"]["phase"] == "regular"


def test_summary_extras():
    out = dashboard_summary(populated_store().get(), TODAY)
    assert [f["ident"] for f in out["flights"]] == ["ACA123", "C-GABC"]
    assert out["flights"][0]["overhead"] is True and out["flights"][1]["overhead"] is False
    assert "lat" not in out["flights"][0]
    assert out["flight_stats"]["sightings"] == 5 and out["flight_stats"]["regulars"][0]["hex"] == "a1"
    assert out["holidays"] == [{"name": "Easter", "display": "Easter", "date": "2026-04-05", "days": 3}]
    assert out["weather"]["current"]["temp"] == 12 and len(out["weather"]["daily"]) == 4
    assert "sunrise" not in out["weather"]["daily"][0]


def test_summary_when_nothing_is_published():
    out = dashboard_summary(SnapshotStore().get(), TODAY)
    assert out == {"today": TODAY, "main_event": None, "sports": [], "flights": None, "flight_stats": None, "holidays": None, "weather": None}


def test_endpoint(tmp_path):
    config = ConfigStore(tmp_path / "config.json")
    store, events = populated_store(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(),)})
    director = Director(config, store, reg, events)
    app = create_app(config, store, reg, director, PreviewHub())
    c = TestClient(app, headers={UI_HEADER: UI_TOKEN}, base_url="http://localhost")
    body = c.get("/api/dashboard").json()
    assert body["sports"][0]["sport"] == "nhl" and len(body["today"]) == 10
    assert body["holidays"][0]["name"] == "Easter"
