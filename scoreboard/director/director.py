"""Decides what is on screen each frame.

Priority: event interrupt (goal...) > pinned boot/error boards > state playlist.
Reads live config every tick so UI edits apply without restart.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image
from pydantic import BaseModel, ValidationError

from ..boards.base import BaseBoard, BoardContext, EventBoard
from ..config import AppConfig, ConfigStore
from ..data import Event, Snapshot, SnapshotStore
from ..data.events import EventBus
from ..plugins import Registry
from ..render.profiles import profile_for
from .brightness import brightness_for
from .playlist import Cursor, advance, available_entries, clamp
from .state import PLAYLIST_STATES, AppState, compute_state
from .transitions import transition

log = logging.getLogger(__name__)

BOOT_BOARD = "splash"
ERROR_BOARD = "clock"
FALLBACK_BOARD = "clock"
BOOT_SECONDS = 4.0
EVENT_MAX_SECONDS = 30.0


class Director:
    def __init__(self, config: ConfigStore, snapshots: SnapshotStore, registry: Registry, events: EventBus) -> None:
        self._config = config
        self._snapshots = snapshots
        self._registry = registry
        self._events = events
        self._cursor: Cursor | None = None       # created on first frame
        self._booted_at = 0.0
        self._active_key: str | None = None
        self._active_event: tuple[Event, EventBoard, float] | None = None
        self._pending: list[Event] = []
        self._board_cfg_cache: dict[tuple[str, int], BaseModel] = {}
        self._last_frame: Image.Image | None = None
        self._transition: tuple[Image.Image, float] | None = None     # (outgoing frame, started_at)
        self._cfg_version = 0
        config.subscribe(self._on_config)

    # -- public --------------------------------------------------------------

    @property
    def state(self) -> AppState:
        return self._cursor.state if self._cursor else AppState.BOOT

    @property
    def active_board(self) -> str | None:
        return self._active_key

    def brightness(self, now: datetime | None = None) -> int:
        cfg = self._config.get()
        now = now or self._now(cfg)
        return brightness_for(now, cfg.brightness, cfg.location, live=self.state == AppState.LIVE)

    def frame(self, mono: float | None = None) -> Image.Image:
        mono = _time.monotonic() if mono is None else mono
        if self._cursor is None:
            self._cursor = Cursor(AppState.BOOT, 0, mono)
            self._booted_at = mono
        cfg = self._config.get()
        snap = self._snapshots.get()
        self._pending.extend(self._events.drain())
        self._sync_state(snap, mono)

        board, key, event = self._select(cfg, snap, mono)
        ctx = self._context(cfg, snap, mono, event)
        board_cfg = self._board_config(cfg, board)
        if key != self._active_key:
            if (self._active_key is not None and self._last_frame is not None and not isinstance(board, EventBoard)
                    and cfg.transition.style != "none" and self._last_frame.size == (cfg.display.width, cfg.display.height)):
                self._transition = (self._last_frame, mono)
            else:
                self._transition = None          # event boards cut in instantly
            self._active_key = key
            board.enter(ctx, board_cfg)
        try:
            frame = board.render(ctx, board_cfg)
        except Exception:
            log.exception("board %s failed to render", key)
            frame = Image.new("RGB", (cfg.display.width, cfg.display.height))
        if self._transition:
            outgoing, started = self._transition
            progress = (mono - started) / cfg.transition.duration
            if progress >= 1.0:
                self._transition = None
            else:
                frame = transition(cfg.transition.style, outgoing, frame, progress)
        self._last_frame = frame
        self._after_render(board, ctx, board_cfg, cfg, mono)
        return frame

    # -- internals -----------------------------------------------------------

    def _on_config(self, _: AppConfig) -> None:
        self._cfg_version += 1
        self._board_cfg_cache.clear()

    def _now(self, cfg: AppConfig) -> datetime:
        try:
            return datetime.now(ZoneInfo(cfg.location.timezone))
        except Exception:  # noqa: BLE001
            return datetime.now().astimezone()

    def _sync_state(self, snap: Snapshot, mono: float) -> None:
        if self._cursor.state == AppState.BOOT:
            if mono - self._booted_at < BOOT_SECONDS:
                return
        new_state = compute_state(snap)
        if new_state != self._cursor.state:
            log.info("state %s -> %s", self._cursor.state.value, new_state.value)
            self._cursor = Cursor(new_state, 0, mono)

    def _select(self, cfg: AppConfig, snap: Snapshot, mono: float) -> tuple[BaseBoard, str, Event | None]:
        boards = self._registry.boards
        if self._active_event:
            event, board, started = self._active_event
            return board, board.key, event
        while self._pending:
            event = self._pending.pop(0)
            for eb in self._registry.event_boards:
                if eb.matches(event, self._board_config(cfg, eb)):
                    self._active_event = (event, eb, mono)
                    return eb, eb.key, event
        state = self._cursor.state
        if state == AppState.BOOT:
            return boards.get(BOOT_BOARD) or boards[FALLBACK_BOARD], BOOT_BOARD, None
        if state == AppState.ERROR:
            return boards[ERROR_BOARD], ERROR_BOARD, None
        entries = available_entries(getattr(cfg.playlists, state.value), set(boards))
        entries = [e for e in entries if snap.has(*boards[e.board].requires)]
        if not entries:
            return boards[FALLBACK_BOARD], FALLBACK_BOARD, None
        self._cursor = clamp(self._cursor, len(entries))
        entry = entries[self._cursor.index]
        return boards[entry.board], entry.board, None

    def _after_render(self, board: BaseBoard, ctx: BoardContext, board_cfg: BaseModel, cfg: AppConfig, mono: float) -> None:
        if self._active_event:
            _, eb, started = self._active_event
            if eb.done(ctx, board_cfg) or mono - started > EVENT_MAX_SECONDS:
                self._active_event = None
                self._cursor = Cursor(self._cursor.state, self._cursor.index, mono)
            return
        if self._cursor.state not in PLAYLIST_STATES:
            return
        entries = available_entries(getattr(cfg.playlists, self._cursor.state.value), set(self._registry.boards))
        if not entries:
            return
        entry = entries[min(self._cursor.index, len(entries) - 1)]
        expired = entry.duration is not None and ctx.elapsed >= entry.duration
        if expired or board.done(ctx, board_cfg):
            self._cursor = advance(self._cursor, len(entries), mono)

    def _context(self, cfg: AppConfig, snap: Snapshot, mono: float, event: Event | None) -> BoardContext:
        entered = self._active_event[2] if self._active_event else self._cursor.entered_at
        return BoardContext(
            snapshot=snap,
            profile=profile_for(cfg.display.width, cfg.display.height),
            width=cfg.display.width,
            height=cfg.display.height,
            fps=cfg.display.fps,
            now=self._now(cfg),
            elapsed=mono - entered,
            event=event,
        )

    def _board_config(self, cfg: AppConfig, board: BaseBoard) -> BaseModel:
        cache_key = (board.key, self._cfg_version)
        cached = self._board_cfg_cache.get(cache_key)
        if cached is not None:
            return cached
        raw = cfg.boards.get(board.key, {})
        try:
            model = board.config_model.model_validate(raw)
        except ValidationError as exc:
            log.warning("invalid config for board %s, using defaults: %s", board.key, exc)
            model = board.config_model()
        self._board_cfg_cache[cache_key] = model
        return model
