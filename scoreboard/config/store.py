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

from .models import AppConfig, deep_merge

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
            return AppConfig.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            broken = self._path.with_suffix(".json.broken")
            log.error("config at %s is invalid (%s); moved to %s, using defaults", self._path, exc, broken)
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
