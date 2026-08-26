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


class BaseBoard:
    key: ClassVar[str] = ""
    title: ClassVar[str] = ""
    config_model: ClassVar[type[BaseModel]] = EmptyConfig
    requires: ClassVar[frozenset[str]] = frozenset()
    sport: ClassVar[str | None] = None      # set on boards that only make sense for one sport's main event

    def enter(self, ctx: BoardContext, cfg: BaseModel) -> None:
        """Called once when the board becomes active; pre-render here."""

    def render(self, ctx: BoardContext, cfg: BaseModel) -> Image.Image:
        raise NotImplementedError

    def done(self, ctx: BoardContext, cfg: BaseModel) -> bool:
        return False


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


class EventBoard(BaseBoard):
    """A board that plays in response to an event (goal, penalty...)."""

    event_kinds: ClassVar[frozenset[str]] = frozenset()

    def matches(self, event: Event, cfg: BaseModel) -> bool:
        return event.kind in self.event_kinds
