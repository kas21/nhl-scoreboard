"""Ticker mode: the playlist as one long strip scrolled through the panel.

Instead of showing one board at a time, boards are laid out side by side and moved
right-to-left past the viewport. Each tile owns its own board *instance* and its own
clock, so two tiles of the same board can be on screen at once and each animates from
its own entrance. Tiles are built a screen ahead, rendered only while they overlap the
viewport, and dropped once they leave on the left.

The strip knows nothing about config, the registry or the snapshot: the director hands
it a :class:`StripFrame` of the values and callbacks it needs for one frame.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image
from pydantic import BaseModel

from ..boards.base import BaseBoard, BoardContext

log = logging.getLogger(__name__)

MAX_STEP = 0.25         # seconds of travel per frame, clamped so a stalled loop can't teleport the strip
LOOKAHEAD = 1.0         # build tiles this many screen-widths past the right edge
MAX_TILES = 24          # backstop against a pathological tile width filling forever
MAX_PARTS = 64          # cap on how many tiles one playlist entry may expand into
MIN_TILE = 8            # narrowest tile we will ask a board to render into


@dataclass
class Tile:
    """One board in the strip, with its own instance and its own clock."""

    key: str
    board: BaseBoard
    x: float                # left edge, in strip coordinates
    width: int
    entered_at: float       # monotonic time this tile's board clock started
    part: int = 0           # which of the board's parts this tile shows

    @property
    def right(self) -> float:
        return self.x + self.width


@dataclass(frozen=True)
class StripFrame:
    """Everything the strip needs to compose one frame. Assembled by the director."""

    keys: tuple[str, ...]                                   # playlist board keys, in order
    width: int
    height: int
    mono: float
    tile_width: int                                         # 0 = full panel width
    speed: float                                            # pixels per second
    gap: int                                                # blank pixels between tiles
    make_board: Callable[[str], BaseBoard | None]                 # key -> a fresh board instance
    make_ctx: Callable[[float, int, int, int], BoardContext]     # (entered_at, w, h, part) -> context
    board_cfg: Callable[[BaseBoard], BaseModel]
    on_error: Callable[[str], None]                         # board key -> quarantine it


class Strip:
    """Mutable scroll state. One instance lives on the director."""

    def __init__(self) -> None:
        self._tiles: list[Tile] = []
        self._offset = 0.0              # strip coordinate of the viewport's left edge
        self._last_mono: float | None = None
        self._next = 0                  # index into ``keys`` of the entry to expand next
        self._queue: list[tuple[str, int]] = []     # (key, part) still to be laid out
        self._probe: tuple[str, BaseBoard] | None = None    # instance built to count parts, reused for part 0
        self._keys: tuple[str, ...] = ()
        self._layout: tuple[int, int, int, int] = (0, 0, 0, 0)   # width, height, tile width, gap

    # -- public --------------------------------------------------------------

    @property
    def current(self) -> str | None:
        """Key of the tile under the middle of the viewport — what the UI calls the active board."""
        middle = self._offset + self._layout[0] / 2
        return next((t.key for t in self._tiles if t.x <= middle < t.right), None)

    def reset(self) -> None:
        self._tiles = []
        self._offset = 0.0
        self._last_mono = None
        self._next = 0
        self._queue = []
        self._probe = None

    def frame(self, f: StripFrame) -> Image.Image:
        self._sync(f)
        self._advance(f)
        self._fill(f)
        self._prune()
        return self._compose(f)

    # -- internals -----------------------------------------------------------

    def _sync(self, f: StripFrame) -> None:
        """Rebuild from scratch when the geometry changes; requeue when the playlist does."""
        layout = (f.width, f.height, self._tile_width(f), f.gap)
        if layout != self._layout:
            self._layout = layout
            self.reset()
        if f.keys != self._keys:
            self._keys = f.keys
            self._next = 0
            self._queue = []
            self._probe = None
            self._tiles = [t for t in self._tiles if t.x < self._offset + f.width]   # keep what is on screen

    def _tile_width(self, f: StripFrame) -> int:
        return max(min(f.tile_width or f.width, f.width), MIN_TILE)

    def _advance(self, f: StripFrame) -> None:
        dt = 0.0 if self._last_mono is None else min(max(f.mono - self._last_mono, 0.0), MAX_STEP)
        self._last_mono = f.mono
        self._offset += f.speed * dt

    def _fill(self, f: StripFrame) -> None:
        """Build tiles until the strip reaches past the right edge by ``LOOKAHEAD`` screens."""
        if not self._keys:
            return
        target = self._offset + f.width * (1.0 + LOOKAHEAD)
        width = self._tile_width(f)
        failed: set[str] = set()            # a board that blew up is not worth retrying this frame
        for _ in range(MAX_TILES):          # bounded: an unbuildable playlist must not spin here
            if len(self._tiles) >= MAX_TILES:
                return
            if self._tiles and self._tiles[-1].right + f.gap >= target:
                return
            if not self._queue and not self._enqueue(f, failed):
                return                      # nothing in the playlist can be laid out right now
            key, part = self._queue.pop(0)
            if key in failed:
                continue
            x = self._tiles[-1].right + f.gap if self._tiles else self._offset
            tile = self._build(f, key, part, x, width)
            if tile is None:
                failed.add(key)
                self._queue = [q for q in self._queue if q[0] != key]
                continue
            self._tiles.append(tile)

    def _enqueue(self, f: StripFrame, failed: set[str]) -> bool:
        """Expand the next playlist entry into one queued tile per part. False when none can be."""
        for _ in range(len(self._keys)):
            key = self._keys[self._next % len(self._keys)]
            self._next += 1
            if key in failed:
                continue
            board = f.make_board(key)
            if board is None:
                failed.add(key)
                continue
            self._probe = (key, board)      # reused as the tile for part 0 rather than thrown away
            self._queue = [(key, part) for part in range(self._count(f, key, board, width=self._tile_width(f)))]
            return True
        return False

    def _count(self, f: StripFrame, key: str, board: BaseBoard, width: int) -> int:
        try:
            parts = board.parts(f.make_ctx(f.mono, width, f.height, 0), f.board_cfg(board))
        except Exception:
            log.exception("board %s failed to count its ticker parts; treating it as one", key)
            return 1
        return max(min(int(parts), MAX_PARTS), 1)

    def _build(self, f: StripFrame, key: str, part: int, x: float, width: int) -> Tile | None:
        board = self._take_probe(key) or f.make_board(key)
        if board is None:
            return None
        tile = Tile(key=key, board=board, x=x, width=width, entered_at=f.mono, part=part)
        try:
            board.enter(f.make_ctx(tile.entered_at, width, f.height, part), f.board_cfg(board))
        except Exception:
            log.exception("board %s failed to enter the ticker; skipping it", key)
            f.on_error(key)
            return None
        return tile

    def _take_probe(self, key: str) -> BaseBoard | None:
        if self._probe and self._probe[0] == key:
            board = self._probe[1]
            self._probe = None
            return board
        return None

    def _prune(self) -> None:
        """Drop tiles that have left on the left, then rebase so the coordinates stay small."""
        self._tiles = [t for t in self._tiles if t.right > self._offset]
        if not self._tiles:
            return
        shift = self._tiles[0].x
        if shift:
            self._offset -= shift
            self._tiles = [Tile(t.key, t.board, t.x - shift, t.width, t.entered_at, t.part) for t in self._tiles]

    def _compose(self, f: StripFrame) -> Image.Image:
        canvas = Image.new("RGB", (f.width, f.height))
        for tile in list(self._tiles):
            left = int(round(tile.x - self._offset))
            if left >= f.width or left + tile.width <= 0:
                continue
            ctx = f.make_ctx(tile.entered_at, tile.width, f.height, tile.part)
            try:
                canvas.paste(tile.board.render(ctx, f.board_cfg(tile.board)), (left, 0))
            except Exception:
                log.exception("board %s failed to render in the ticker; skipping it", tile.key)
                f.on_error(tile.key)
                self._tiles = [t for t in self._tiles if t is not tile]
        return canvas
