"""Discover boards and data sources: built-ins plus ``scoreboard.*`` entry points."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points

from .boards.base import BaseBoard, EventBoard
from .data.events import Detector
from .data.source import DataSource

log = logging.getLogger(__name__)


@dataclass
class Registry:
    boards: dict[str, BaseBoard] = field(default_factory=dict)
    sources: dict[str, DataSource] = field(default_factory=dict)
    detectors: list[Detector] = field(default_factory=list)

    @property
    def event_boards(self) -> list[EventBoard]:
        return [b for b in self.boards.values() if isinstance(b, EventBoard)]

    def board_models(self) -> dict[str, type]:
        return {k: b.config_model for k, b in self.boards.items()}

    def source_models(self) -> dict[str, type]:
        return {k: s.config_model for k, s in self.sources.items()}


def load_registry() -> Registry:
    reg = Registry()
    for ep in entry_points(group="scoreboard.boards"):
        _load(ep, reg.boards, "board")
    for ep in entry_points(group="scoreboard.sources"):
        _load(ep, reg.sources, "source")
    for ep in entry_points(group="scoreboard.detectors"):
        try:
            reg.detectors.append(ep.load())
        except Exception:
            log.exception("failed to load detector %s", ep.name)
    log.info("loaded boards=%s sources=%s", sorted(reg.boards), sorted(reg.sources))
    return reg


def _load(ep, target: dict, kind: str) -> None:
    try:
        cls = ep.load()
        instance = cls()
        key = getattr(instance, "key", None) or ep.name
        target[key] = instance
    except Exception:
        log.exception("failed to load %s plugin %s", kind, ep.name)
