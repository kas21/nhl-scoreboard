"""The flights sighting log: visits per airframe, persisted, tolerant of a bad file."""

import json

from scoreboard.extras.flights.sightings import SightingLog

T0 = 1_700_000_000.0
DAY = "2026-09-04"


def ac(hex_, reg="", **extra):
    return {"hex": hex_, "registration": reg, "type": "C172", "operator": "", "lat": 1, "lon": 2, **extra}


def test_one_visit_counts_once_until_the_gap_passes(tmp_path):
    log = SightingLog(tmp_path / "s.json")
    first = log.record([ac("a1", "C-GABC")], T0, DAY, gap_seconds=1800)
    assert first[0]["sightings"] == 1 and first[0]["first_seen"] == T0
    same = log.record([ac("a1", "C-GABC")], T0 + 60, DAY, gap_seconds=1800)         # still overhead: same visit
    assert same[0]["sightings"] == 1
    later = log.record([ac("a1", "C-GABC")], T0 + 60 + 1801, DAY, gap_seconds=1800)  # came back: a new visit
    assert later[0]["sightings"] == 2 and later[0]["first_seen"] == T0
    stats = log.stats(DAY)
    assert stats["airframes"] == 1 and stats["sightings"] == 2 and stats["today"] == 2 and stats["since"] == T0
    assert stats["regulars"][0] == {"hex": "a1", "registration": "C-GABC", "type": "C172", "operator": "", "count": 2, "last_seen": T0 + 60 + 1801}


def test_aircraft_without_hex_pass_through_and_details_fill_in(tmp_path):
    log = SightingLog(tmp_path / "s.json")
    out = log.record([{"lat": 1, "lon": 2}, ac("b2")], T0, DAY)
    assert "sightings" not in out[0] and out[1]["sightings"] == 1
    log.record([ac("b2", "N123AB", operator="Air Canada")], T0 + 10, DAY)
    assert log.stats(DAY)["regulars"][0]["registration"] == "N123AB" and log.stats(DAY)["regulars"][0]["operator"] == "Air Canada"
    log.record([ac("b2", "")], T0 + 20, DAY)
    assert log.stats(DAY)["regulars"][0]["registration"] == "N123AB"                  # a blank later poll does not erase it


def test_persists_and_reloads(tmp_path):
    path = tmp_path / "flights" / "sightings.json"
    log = SightingLog(path, save_interval=0)
    log.record([ac("a1", "C-GABC"), ac("b2")], T0, DAY)
    assert path.is_file()
    again = SightingLog(path)
    assert again.stats(DAY) == log.stats(DAY)
    again.record([ac("a1", "C-GABC")], T0 + 7200, DAY)
    assert again.stats(DAY)["sightings"] == 3


def test_debounced_save_and_flush(tmp_path):
    path = tmp_path / "s.json"
    log = SightingLog(path, save_interval=60)
    log.record([ac("a1")], T0, DAY)
    assert path.is_file()                                             # first write is immediate
    log.record([ac("b2")], T0 + 30, DAY)
    assert "b2" not in json.loads(path.read_text())["airframes"]        # within the debounce: not yet on disk
    log.flush()
    assert "b2" in json.loads(path.read_text())["airframes"]


def test_broken_file_is_set_aside(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    log = SightingLog(path)
    assert log.stats(DAY)["airframes"] == 0
    assert path.with_suffix(".json.broken").is_file() and not path.exists()
    path.write_text(json.dumps({"airframes": {"ok": {"count": 3, "last_seen": T0, "first_seen": T0}, "junk": {"count": "x"}}, "daily": {DAY: 3}}))
    log = SightingLog(path)
    assert log.stats(DAY) == {"airframes": 1, "sightings": 3, "today": 3, "since": T0,
                              "regulars": [{"hex": "ok", "count": 3, "last_seen": T0}]}


def test_cap_keeps_the_regulars(tmp_path):
    log = SightingLog(tmp_path / "s.json", max_airframes=3)
    log.record([ac("reg")], T0, DAY)
    log.record([ac("reg")], T0 + 4000, DAY)                            # 2 visits
    for i in range(5):
        log.record([ac(f"once{i}")], T0 + 5000 + i, DAY)
    stats = log.stats(DAY)
    assert stats["airframes"] == 3 and stats["regulars"][0]["hex"] == "reg"
    assert {r["hex"] for r in stats["regulars"]} == {"reg", "once4", "once3"}      # the most recent one-offs survive
