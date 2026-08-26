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


def test_migration_bumps_version(tmp_path):
    from scoreboard.config import store as store_mod
    store_mod.MIGRATIONS[1] = lambda d: {**d, "brightness": {"day": 55}}
    store_mod.CONFIG_VERSION = 2
    try:
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"version": 1}))
        cfg = ConfigStore(path).get()
        assert cfg.brightness.day == 55
    finally:
        del store_mod.MIGRATIONS[1]
        store_mod.CONFIG_VERSION = 1
