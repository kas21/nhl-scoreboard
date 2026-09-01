"""Persistent, validated, observable config store.

Reads/writes a single ``config.json``. Writes are atomic (temp + rename) and
keep a small ring of backups. Subscribers are notified after every change so
the running app picks edits up immediately.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import CONFIG_VERSION, AppConfig, deep_merge

log = logging.getLogger(__name__)

BACKUP_COUNT = 5
Listener = Callable[[AppConfig], None]


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._listeners: list[Listener] = []
        self._config = self._load()

    @property
    def path(self) -> Path:
        return self._path

    def get(self) -> AppConfig:
        return self._config

    def subscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def update(self, patch: dict[str, Any]) -> AppConfig:
        """Deep-merge ``patch`` into the current config, validate, persist, notify.

        Raises ``pydantic.ValidationError`` on bad input; nothing is written then.
        """
        with self._lock:
            merged = deep_merge(self._config.model_dump(mode="json"), patch)
            new = AppConfig.model_validate(merged)
            self._write(new)
            self._config = new
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(new)
            except Exception:
                log.exception("config listener failed")
        return new

    def replace(self, document: dict[str, Any]) -> AppConfig:
        """Replace the whole document (import / reset)."""
        with self._lock:
            new = AppConfig.model_validate(document)
            self._write(new)
            self._config = new
            listeners = list(self._listeners)
        for listener in listeners:
            listener(new)
        return new

    def reset(self) -> AppConfig:
        return self.replace({})

    # -- persistence ---------------------------------------------------------

    def _load(self) -> AppConfig:
        if not self._path.exists():
            log.info("no config at %s, using defaults", self._path)
            cfg = AppConfig()
            self._write(cfg)
            return cfg
        try:
            raw = json.loads(self._path.read_text())
        except json.JSONDecodeError as exc:
            return self._reset_broken(f"not valid JSON: {exc}")
        if not isinstance(raw, dict):
            return self._reset_broken("top level is not an object")
        raw = migrate(raw)
        cfg, dropped = salvage(raw)
        if cfg is None:
            return self._reset_broken("could not salvage any settings")
        if dropped:
            log.warning("config: dropped invalid settings %s (backup kept as %s)", dropped, self._path.with_suffix(".json.1"))
            self._write(cfg)
        elif raw.get("version") != cfg.version:
            self._write(cfg)                                    # persist migration
        return cfg

    def _reset_broken(self, reason: str) -> AppConfig:
        broken = self._path.with_suffix(".json.broken")
        log.error("config at %s is unusable (%s); moved to %s, using defaults", self._path, reason, broken)
        os.replace(self._path, broken)
        cfg = AppConfig()
        self._write(cfg)
        return cfg

    def _write(self, cfg: AppConfig) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_backups()
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg.model_dump(mode="json"), indent=2) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)

    def _rotate_backups(self) -> None:
        if not self._path.exists():
            return
        for i in range(BACKUP_COUNT - 1, 0, -1):
            src = self._path.with_suffix(f".json.{i}")
            if src.exists():
                os.replace(src, self._path.with_suffix(f".json.{i + 1}"))
        os.replace(self._path, self._path.with_suffix(".json.1"))


# -- migration & salvage -------------------------------------------------------

def _holiday_overrides(doc: dict[str, Any]) -> dict[str, Any]:
    """1 -> 2: ``sources.holidays.disabled`` (a list of names) became an overrides map.

    The map holds everything you can change about one holiday — hide it, rename it,
    give it a different picture — so hiding is now ``{"enabled": false}``.
    """
    sources = doc.get("sources")
    holidays = sources.get("holidays") if isinstance(sources, dict) else None
    if not isinstance(holidays, dict) or "disabled" not in holidays:
        return doc
    kept = {k: v for k, v in holidays.items() if k != "disabled"}
    overrides = dict(kept.get("overrides") or {})
    for name in holidays.get("disabled") or []:
        overrides[str(name)] = {**overrides.get(str(name), {}), "enabled": False}
    return {**doc, "sources": {**sources, "holidays": {**kept, "overrides": overrides}}}


MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    1: _holiday_overrides,
}


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply versioned migrations to bring an old document up to CONFIG_VERSION."""
    doc = dict(raw)
    version = int(doc.get("version") or 1)
    while version < CONFIG_VERSION:
        step = MIGRATIONS.get(version)
        doc = step(doc) if step else doc
        version += 1
        doc["version"] = version
    return doc


def salvage(raw: dict[str, Any], max_rounds: int = 50) -> tuple[AppConfig | None, list[str]]:
    """Validate, dropping only the offending keys (unknown or invalid) until it passes.

    Returns (config, dropped_paths). A user never loses their whole config
    because one field was renamed or one value went out of range.
    """
    doc = json.loads(json.dumps(raw))
    dropped: list[str] = []
    for _ in range(max_rounds):
        try:
            return AppConfig.model_validate(doc), dropped
        except ValidationError as exc:
            progressed = False
            for err in exc.errors():
                loc = [str(p) for p in err["loc"]]
                if not loc:
                    continue
                if _delete_path(doc, loc):
                    dropped.append(".".join(loc))
                    progressed = True
            if not progressed:
                return None, dropped
    return None, dropped


def _delete_path(doc: dict[str, Any], loc: list[str]) -> bool:
    node: Any = doc
    for key in loc[:-1]:
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            return False
    last = loc[-1]
    if isinstance(node, dict) and last in node:
        del node[last]
        return True
    if isinstance(node, list) and last.isdigit() and int(last) < len(node):
        del node[int(last)]
        return True
    return False
