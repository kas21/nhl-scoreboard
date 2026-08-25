"""Playlist cursor: which board to show in the current state and when to advance."""
from __future__ import annotations

from dataclasses import dataclass, replace

from ..config.models import PlaylistEntry
from .state import AppState


@dataclass(frozen=True)
class Cursor:
    state: AppState
    index: int
    entered_at: float

    @property
    def board(self) -> str | None:
        return None


def available_entries(entries: tuple[PlaylistEntry, ...], loaded: set[str]) -> list[PlaylistEntry]:
    return [e for e in entries if e.enabled and e.board in loaded]


def advance(cursor: Cursor, count: int, now: float) -> Cursor:
    if count == 0:
        return replace(cursor, index=0, entered_at=now)
    return replace(cursor, index=(cursor.index + 1) % count, entered_at=now)


def clamp(cursor: Cursor, count: int) -> Cursor:
    if count and cursor.index >= count:
        return replace(cursor, index=0)
    return cursor
