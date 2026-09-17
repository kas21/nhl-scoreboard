"""Board contract.

A board is a pure renderer: given the snapshot, its own validated config and
the time since it was shown, return a frame. It must not touch the network,
the wall clock (use ``ctx.now``), or the matrix.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Protocol, runtime_checkable

from PIL import Image
from pydantic import BaseModel

from ..data import Event, Snapshot
from ..render.anim import Sequence
from ..render.profiles import SizeProfile


class EmptyConfig(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}


@dataclass(frozen=True)
class BoardContext:
    snapshot: Snapshot
    profile: SizeProfile
    width: int
    height: int
    fps: int
    now: datetime          # local wall-clock time (tz-aware)
    elapsed: float         # seconds since this board was entered
    event: Event | None = None
    pace: float | None = None      # playlist's seconds per item for a paced board (None = the board's default)


@runtime_checkable
class Board(Protocol):
    key: ClassVar[str]
    title: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]
    requires: ClassVar[frozenset[str]]

    def render(self, ctx: BoardContext, cfg: BaseModel) -> Image.Image: ...

    def done(self, ctx: BoardContext, cfg: BaseModel) -> bool:
        """Self-terminating boards (tickers) return True when finished."""
        ...

    def auto_seconds(self, ctx: BoardContext, cfg: BaseModel) -> float | None:
        """How long a run lasts when the playlist duration is "auto" (None = never ends itself)."""
        ...

    def auto_items(self, ctx: BoardContext, cfg: BaseModel) -> tuple[int, str] | None:
        """What a run is made of: ``(count, unit)`` such as ``(7, "game")``, or None."""
        ...


class BaseBoard:
    key: ClassVar[str] = ""
    title: ClassVar[str] = ""
    config_model: ClassVar[type[BaseModel]] = EmptyConfig
    requires: ClassVar[frozenset[str]] = frozenset()
    sport: ClassVar[str | None] = None      # set on boards that only make sense for one sport's main event
    # False for boards that only draw something with an event behind them: the director keeps
    # them out of the rotation and the web UI out of the playlist pickers.
    playlistable: ClassVar[bool] = True
    # Set on a board that shows a list one item at a time (games, aircraft, holidays, alerts).
    # The playlist's seconds then mean seconds per item, handed over as ``ctx.pace``, and the
    # board decides when it is done; for every other board the seconds are the whole run.
    pace_unit: ClassVar[str | None] = None

    def enter(self, ctx: BoardContext, cfg: BaseModel) -> None:
        """Called once when the board becomes active; pre-render here."""

    def render(self, ctx: BoardContext, cfg: BaseModel) -> Image.Image:
        raise NotImplementedError

    def done(self, ctx: BoardContext, cfg: BaseModel) -> bool:
        return False

    def auto_seconds(self, ctx: BoardContext, cfg: BaseModel) -> float | None:
        """Seconds a run lasts when the playlist entry says "auto" — for the web UI, which
        would otherwise only be able to say "auto" and leave the length a mystery.

        None means the board never ends on its own: on "auto" the playlist stays on it
        until the app state changes or an event interrupts. Boards that override ``done``
        should override this too, with the same length ``done`` waits for.
        """
        return None

    def auto_items(self, ctx: BoardContext, cfg: BaseModel) -> tuple[int, str] | None:
        """What the run is made of, as ``(count, unit)`` with a singular unit: ``(7, "game")``,
        ``(4, "aircraft")``, ``(3, "page")``. For the web UI's rotation view, which shows the
        count above each board's slice of the lap. None for a board with no natural count."""
        return None


def per_item(ctx: BoardContext, default: float) -> float:
    """Seconds each item stays up: the playlist's number when the entry has one, else the
    board's own setting. For boards with a ``pace_unit``."""
    return default if ctx.pace is None else ctx.pace


class SequenceMixin:
    """For boards that are one pre-rendered timeline: implement ``build(ctx, cfg) -> Sequence``.

    Handles caching, rebuilding on size change, playback and completion.
    """

    _seq: Sequence | None = None
    _seq_size: tuple[int, int] = (0, 0)

    def build(self, ctx: BoardContext, cfg: BaseModel) -> Sequence:
        raise NotImplementedError

    def enter(self, ctx: BoardContext, cfg: BaseModel) -> None:
        self._seq = self.build(ctx, cfg)
        self._seq_size = (ctx.width, ctx.height)

    def render(self, ctx: BoardContext, cfg: BaseModel) -> Image.Image:
        if self._seq is None or self._seq_size != (ctx.width, ctx.height):
            self.enter(ctx, cfg)
        return self._seq.at(ctx.elapsed)  # type: ignore[union-attr]

    def done(self, ctx: BoardContext, cfg: BaseModel) -> bool:
        return self._seq is not None and self._seq.finished(ctx.elapsed)

    def auto_seconds(self, ctx: BoardContext, cfg: BaseModel) -> float | None:
        return self._seq.duration if self._seq is not None else None


class EventBoard(BaseBoard):
    """A board that plays in response to an event (goal, penalty...)."""

    event_kinds: ClassVar[frozenset[str]] = frozenset()
    playlistable: ClassVar[bool] = False

    def matches(self, event: Event, cfg: BaseModel) -> bool:
        return event.kind in self.event_kinds
