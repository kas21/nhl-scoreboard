"""Playlist cursor: which board to show in the current state and when to advance."""
from __future__ import annotations

from collections.abc import Collection
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


def available_entries(entries: tuple[PlaylistEntry, ...], loaded: set[str],
                      not_playlistable: Collection[str] = ()) -> list[PlaylistEntry]:
    """Enabled entries whose board is loaded and can rotate. Interrupt boards are left out:
    they only draw something with an event behind them, so in a playlist they would hold a
    blank frame for their whole sequence."""
    return [e for e in entries if e.enabled and e.board in loaded and e.board not in not_playlistable]


def advance(cursor: Cursor, count: int, now: float) -> Cursor:
    if count == 0:
        return replace(cursor, index=0, entered_at=now)
    return replace(cursor, index=(cursor.index + 1) % count, entered_at=now)


def clamp(cursor: Cursor, count: int) -> Cursor:
    if count and cursor.index >= count:
        return replace(cursor, index=0)
    return cursor
