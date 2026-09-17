import json

import pytest
from pydantic import ValidationError

from scoreboard.config import AppConfig, ConfigStore
from scoreboard.config.models import deep_merge


def test_defaults_written_on_first_load(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    assert (tmp_path / "config.json").exists()
    assert store.get() == AppConfig()


def test_update_merges_validates_persists_and_notifies(config_store):
    seen = []
    config_store.subscribe(seen.append)
    cfg = config_store.update({"brightness": {"day": 42}})
    assert cfg.brightness.day == 42
    assert cfg.display.width == 128            # untouched sibling kept
    assert seen == [cfg]
    reloaded = ConfigStore(config_store.path).get()
    assert reloaded.brightness.day == 42


def test_invalid_update_rejected_and_not_written(config_store):
    before = config_store.path.read_text()
    with pytest.raises(ValidationError):
        config_store.update({"brightness": {"day": 500}})
    assert config_store.path.read_text() == before
    assert config_store.get().brightness.day == 80


def test_unknown_keys_rejected(config_store):
    with pytest.raises(ValidationError):
        config_store.update({"display": {"bogus": 1}})


def test_corrupt_file_moved_aside(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    store = ConfigStore(path)
    assert store.get() == AppConfig()
    assert (tmp_path / "config.json.broken").exists()


def test_backups_rotate(config_store):
    for i in range(1, 8):
        config_store.update({"brightness": {"day": i}})
    backups = sorted(p.name for p in config_store.path.parent.glob("config.json.[0-9]"))
    assert backups == ["config.json.1", "config.json.2", "config.json.3", "config.json.4", "config.json.5"]
    assert json.loads((config_store.path.parent / "config.json.1").read_text())["brightness"]["day"] == 6


def test_deep_merge_is_non_mutating():
    base = {"a": {"b": 1, "c": 2}}
    out = deep_merge(base, {"a": {"b": 9}})
    assert out == {"a": {"b": 9, "c": 2}}
    assert base == {"a": {"b": 1, "c": 2}}


def test_salvage_drops_only_bad_keys(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"display": {"width": 64, "renamed_field": 1, "pwm_bits": 99}, "brightness": {"day": 42}}))
    cfg = ConfigStore(path).get()
    assert cfg.display.width == 64 and cfg.brightness.day == 42       # good values survive
    assert cfg.display.pwm_bits == 11                                  # invalid -> default
    assert not (tmp_path / "config.json.broken").exists()
    assert json.loads(path.read_text())["display"].get("renamed_field") is None


def test_migration_bumps_version(tmp_path, monkeypatch):
    from scoreboard.config import store as store_mod
    monkeypatch.setitem(store_mod.MIGRATIONS, store_mod.CONFIG_VERSION,
                        lambda d: {**d, "brightness": {"day": 55}})
    monkeypatch.setattr(store_mod, "CONFIG_VERSION", store_mod.CONFIG_VERSION + 1)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": store_mod.CONFIG_VERSION - 1}))
    cfg = ConfigStore(path).get()
    assert cfg.brightness.day == 55
    assert json.loads(path.read_text())["version"] == store_mod.CONFIG_VERSION      # persisted, not redone every start


def test_migration_folds_disabled_holidays_into_overrides(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": 1, "sources": {
        "holidays": {"country": "CA", "disabled": ["Christmas Day", "Labor Day"]},
        "weather": {"units": "metric"}}}))
    holidays = ConfigStore(path).get().sources["holidays"]
    assert holidays["overrides"] == {"Christmas Day": {"enabled": False}, "Labor Day": {"enabled": False}}
    assert holidays["country"] == "CA"                        # siblings survive
    assert "disabled" not in holidays
    assert ConfigStore(path).get().sources["weather"] == {"units": "metric"}


def test_migration_leaves_a_config_without_holidays_alone(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": 1, "brightness": {"day": 42}}))
    cfg = ConfigStore(path).get()
    assert cfg.brightness.day == 42 and cfg.sources == {}


def test_log_level_changes_apply_without_a_restart(tmp_path):
    """The listener was once registered inside request_restart(), so it never fired."""
    import logging

    from scoreboard.app import Application

    root = logging.getLogger()
    original = root.level
    try:
        app = Application(tmp_path / "config.json", output_mode="none")
        app.config.update({"log_level": "DEBUG"})
        assert root.level == logging.DEBUG
        app.config.update({"log_level": "WARNING"})
        assert root.level == logging.WARNING
    finally:
        root.setLevel(original)


def test_migration_clears_a_big_cap_on_a_paced_board_and_keeps_a_small_one(tmp_path):
    """Version 3: a ticker's playlist seconds are per game now. 15 was meant that way and stays;
    a 120 s cap would be 120 s per game, so it goes back to the board's own pace. The clock is
    not paced and keeps its number."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": 2, "playlists": {"offday": [
        {"board": "nhl.ticker", "duration": 15}, {"board": "nfl.ticker", "duration": 120},
        {"board": "clock", "duration": 120}, {"board": "flights.nearby", "duration": None}]}}))
    entries = ConfigStore(path).get().playlists.offday
    assert [(e.board, e.duration) for e in entries] == [("nhl.ticker", 15), ("nfl.ticker", None), ("clock", 120), ("flights.nearby", None)]
    assert json.loads(path.read_text())["version"] == 3


def test_midnight_and_out_of_range_night_times(tmp_path):
    """24:00 is what people type for midnight; 25:00 must not get as far as the render loop."""
    from datetime import UTC, datetime

    from scoreboard.config.models import BrightnessConfig
    from scoreboard.director.brightness import brightness_for

    assert BrightnessConfig(mode="hours", night_start="24:00").night_start == "00:00"
    with pytest.raises(ValidationError):
        BrightnessConfig(mode="hours", night_start="25:00")
    with pytest.raises(ValidationError):
        BrightnessConfig(mode="hours", night_end="23:60")
    # A file written before validation existed still boots: the window is just never night.
    stale = BrightnessConfig.model_construct(mode="hours", day=80, night=10, night_start="25:00", night_end="07:00",
                                             sunset_offset_minutes=0, keep_bright_when_live=True)
    from scoreboard.config.models import LocationConfig
    assert brightness_for(datetime(2026, 1, 1, 23, 0, tzinfo=UTC), stale, LocationConfig(), live=False) == 80
    # ...and salvage drops the bad key rather than the whole file.
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"brightness": {"mode": "hours", "night_start": "25:00", "day": 42}}))
    cfg = ConfigStore(path).get()
    assert cfg.brightness.day == 42 and cfg.brightness.night_start == "22:00"


def test_failed_write_keeps_the_live_config(config_store, monkeypatch):
    config_store.update({"brightness": {"day": 42}})
    import builtins
    real_open = builtins.open

    def full_disk(path, *args, **kwargs):
        if str(path).endswith(".json.tmp"):
            raise OSError(28, "No space left on device")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", full_disk)
    with pytest.raises(OSError):
        config_store.update({"brightness": {"day": 50}})
    monkeypatch.undo()
    assert config_store.path.exists()
    assert json.loads(config_store.path.read_text())["brightness"]["day"] == 42
    assert config_store.get().brightness.day == 42
    assert ConfigStore(config_store.path).get().brightness.day == 42


def test_salvage_survives_a_missing_required_field_and_a_garbage_version(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": "x", "brightness": {"day": 42},
                                "playlists": {"offday": [{"duration": 5}, {"board": "clock", "duration": 7}]}}))
    cfg = ConfigStore(path).get()
    assert cfg.brightness.day == 42                                          # nothing else was lost
    assert [e.board for e in cfg.playlists.offday] == ["clock"]              # only the entry without a board went
    assert not (tmp_path / "config.json.broken").exists()
