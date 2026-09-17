"""Decides what is on screen each frame.

Priority: event interrupt (goal...) > pinned boot/error boards > state playlist.
Reads live config every tick so UI edits apply without restart.
"""
from __future__ import annotations

import logging
import time as _time
from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image
from pydantic import BaseModel, ValidationError

from ..boards.base import BaseBoard, BoardContext, EventBoard
from ..config import AppConfig, ConfigStore
from ..config.models import PlaylistEntry
from ..data import Event, Snapshot, SnapshotStore
from ..data.events import EventBus
from ..plugins import Registry
from ..render.profiles import profile_for
from .brightness import brightness_for
from .playlist import Cursor, advance, clamp
from .state import PLAYLIST_STATES, AppState, compute_state, is_offline
from .transitions import transition

log = logging.getLogger(__name__)


class _DarkBoard(BaseBoard):
    """Last resort when even the fallback board is missing.

    `plugins._load` deliberately swallows a broken entry point so one bad plugin cannot
    stop the app — which means `clock` and `splash` can be absent at runtime. The lookups
    that assumed otherwise raised every frame, and because the render loop catches per
    frame, that showed up as a black panel on a service still reporting itself healthy.
    Better to draw the blank frame honestly and keep the loop's error count meaningful.
    """

    key = "unavailable"
    title = "Unavailable"

    def render(self, ctx: BoardContext, cfg: BaseModel) -> Image.Image:
        return Image.new("RGB", (ctx.width, ctx.height), (0, 0, 0))


DARK_BOARD = _DarkBoard()

BOOT_BOARD = "splash"
ERROR_BOARD = "clock"
FALLBACK_BOARD = "clock"
BOOT_SECONDS = 4.0
EVENT_MAX_SECONDS = 30.0
QUARANTINE_SECONDS = 60.0       # a board that raises is skipped for this long
STALE_DOT = (200, 40, 40)


class Director:
    def __init__(self, config: ConfigStore, snapshots: SnapshotStore, registry: Registry, events: EventBus) -> None:
        self._config = config
        self._snapshots = snapshots
        self._registry = registry
        self._events = events
        self._cursor: Cursor | None = None       # created on first frame
        self._booted_at = 0.0
        self._active_key: str | None = None
        self._entered_event: Event | None = None   # the event the active board was entered with
        self._active_event: tuple[Event, EventBoard, float] | None = None
        self._pending: list[Event] = []
        self._board_cfg_cache: dict[tuple[str, int], BaseModel] = {}
        self._last_frame: Image.Image | None = None
        self._quarantine: dict[str, float] = {}       # board key -> monotonic time it may run again
        self._not_playlistable = frozenset(k for k, b in registry.boards.items() if not b.playlistable)
        self._override: tuple[str, float] | None = None   # (board key, monotonic expiry) forced by the UI
        self._transition: tuple[Image.Image, float] | None = None     # (outgoing frame, started_at)
        self._cfg_version = 0
        self._playlists = config.get().playlists
        self._warn_unplayable(config.get())
        config.subscribe(self._on_config)

    # -- public --------------------------------------------------------------

    @property
    def state(self) -> AppState:
        return self._cursor.state if self._cursor else AppState.BOOT

    @property
    def active_board(self) -> str | None:
        return self._active_key

    def set_override(self, board: str | None, seconds: float = 60.0) -> None:
        """Force ``board`` onto the display for ``seconds`` (None clears). Used by the setup wizard / previews."""
        if board is None or board not in self._registry.boards:
            self._override = None
            return
        self._override = (board, _time.monotonic() + seconds)

    @property
    def override(self) -> str | None:
        if self._override and _time.monotonic() < self._override[1]:
            return self._override[0]
        self._override = None
        return None

    def auto_seconds(self, board: BaseBoard) -> float | None:
        """How long ``board`` runs on an "auto" playlist entry, for the web UI.

        None means it never ends itself (or has not been built yet, so its length is not
        knowable). Read-only: it builds a context but never enters or renders the board.
        """
        cfg = self._config.get()
        try:
            ctx = self._context(cfg, self._snapshots.get(), _time.monotonic(), None)
            return board.auto_seconds(ctx, self._board_config(cfg, board))
        except Exception:       # a board must never be able to break the settings page
            log.debug("auto_seconds failed for board %s", board.key, exc_info=True)
            return None

    def auto_items(self, board: BaseBoard) -> tuple[int, str] | None:
        """What a run of ``board`` is made of right now, ``(count, unit)``, for the web UI. Read-only."""
        cfg = self._config.get()
        try:
            ctx = self._context(cfg, self._snapshots.get(), _time.monotonic(), None)
            return board.auto_items(ctx, self._board_config(cfg, board))
        except Exception:
            log.debug("auto_items failed for board %s", board.key, exc_info=True)
            return None

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

        board, key, event, entry = self._select(cfg, snap, mono)
        # A new event on the board already showing is a switch too: a second goal must not
        # replay the first one's cached timeline, and a real alert arriving behind a UI
        # preview of the alert board must not play the preview's placeholder.
        switching = key != self._active_key or event is not self._entered_event
        if switching and not isinstance(board, EventBoard) and not self._active_event:
            self._cursor = Cursor(self._cursor.state, self._cursor.index, mono)       # the new board's clock starts now
        ctx = self._context(cfg, snap, mono, event, pace=self._pace(board, entry))
        board_cfg = self._board_config(cfg, board)
        if switching:
            if (self._active_key is not None and self._last_frame is not None and not isinstance(board, EventBoard)
                    and cfg.transition.style != "none" and self._last_frame.size == (cfg.display.width, cfg.display.height)):
                self._transition = (self._last_frame, mono)
            else:
                self._transition = None          # event boards cut in instantly
            self._active_key, self._entered_event = key, event
            board.enter(ctx, board_cfg)
        try:
            frame = board.render(ctx, board_cfg)
        except Exception:
            log.exception("board %s failed to render; skipping it for %ss", key, QUARANTINE_SECONDS)
            self._quarantine[key] = mono + QUARANTINE_SECONDS
            self._active_event = None
            self._cursor = advance(self._cursor, 10**6, mono)     # move on; count is re-clamped next frame
            frame = self._last_frame or Image.new("RGB", (cfg.display.width, cfg.display.height))
        if self._transition:
            outgoing, started = self._transition
            progress = (mono - started) / cfg.transition.duration
            if progress >= 1.0:
                self._transition = None
            else:
                frame = transition(cfg.transition.style, outgoing, frame, progress)
        self._last_frame = frame
        if is_offline(snap):
            frame = _stale_marker(frame)
        self._after_render(board, ctx, board_cfg, cfg, mono)
        return frame

    # -- internals -----------------------------------------------------------

    def _on_config(self, cfg: AppConfig) -> None:
        # Called on the web thread while the render thread reads the cache, so the dict is
        # replaced rather than mutated: a reader either sees the whole old map or the whole
        # new one, and never a `clear()` in progress. Same reason the snapshot is immutable.
        self._cfg_version += 1
        self._board_cfg_cache = {}
        if cfg.playlists != self._playlists:          # not on every brightness tweak
            self._playlists = cfg.playlists
            self._warn_unplayable(cfg)

    def _warn_unplayable(self, cfg: AppConfig) -> None:
        """Say so whenever a saved playlist lists a board that cannot rotate (an old config, a
        hand edit): the entry is passed over rather than drawn empty, and the UI marks it."""
        for state in PLAYLIST_STATES:
            for e in getattr(cfg.playlists, state.value):
                if e.board in self._not_playlistable:
                    log.warning("playlist %s lists %s, an interrupt board; skipping it (it plays on its event instead)", state.value, e.board)

    def _now(self, cfg: AppConfig) -> datetime:
        try:
            return datetime.now(ZoneInfo(cfg.location.timezone))
        except Exception:
            return datetime.now().astimezone()

    def _sync_state(self, snap: Snapshot, mono: float) -> None:
        if self._cursor.state == AppState.BOOT:
            if mono - self._booted_at < BOOT_SECONDS:
                return
        new_state = compute_state(snap)
        if new_state != self._cursor.state:
            log.info("state %s -> %s", self._cursor.state.value, new_state.value)
            self._cursor = Cursor(new_state, 0, mono)

    def _usable(self, mono: float) -> set[str]:
        self._quarantine = {k: until for k, until in self._quarantine.items() if until > mono}
        return {k for k in self._registry.boards if k not in self._quarantine}

    def _entries(self, cfg: AppConfig, snap: Snapshot, state: AppState, usable: set[str]) -> list:
        """Playlist entries that are enabled, loaded, not quarantined, and whose required data is non-empty."""
        entries = getattr(cfg.playlists, state.value)
        return [e for e in entries if self._skip_reason(e, snap, usable) is None]

    def _skip_reason(self, entry, snap: Snapshot, usable: set[str]) -> str | None:
        """Why the director passes ``entry`` over right now, or None when it is in the rotation.

        One place for the rule so the rotation view on the dashboard says exactly what the
        frame loop does (the first three checks are ``available_entries``; the rest depend
        on the snapshot).
        """
        boards = self._registry.boards
        if not entry.enabled:
            return "disabled"
        if entry.board not in boards:
            return "not loaded"
        if entry.board in self._not_playlistable:
            return "interrupt board, plays on its event"
        if entry.board not in usable:
            return "paused after an error"
        board = boards[entry.board]
        if not all(snap.get(k) for k in board.requires):
            return "no data"
        main = snap.get("main_event") or {}
        if board.sport is not None and "main_event" in board.requires and main.get("sport") != board.sport:
            return "another sport's game"
        return None

    def rotation(self, mono: float | None = None) -> dict[str, Any]:
        """The current state's playlist as the director sees it, for the dashboard's rotation view.

        Every configured entry is listed in order with its effective length (the fixed
        duration, or what "auto" works out to right now; for a paced board the number is
        seconds per item and the length follows), what it is made of, and why it is skipped
        if it is. ``index`` is the cursor's position among the entries that are not
        skipped; ``elapsed`` is how long the active one has been up. Read-only, like
        ``auto_seconds``: it never enters or renders a board.
        """
        mono = _time.monotonic() if mono is None else mono
        cfg = self._config.get()
        snap = self._snapshots.get()
        boards = self._registry.boards
        state = self.state
        usable = {k for k in boards if self._quarantine.get(k, 0.0) <= mono}
        configured = getattr(cfg.playlists, state.value, ()) if state in PLAYLIST_STATES else ()
        try:
            ctx = self._context(cfg, snap, mono, None)
        except Exception:
            ctx = None
        entries: list[dict[str, Any]] = []
        for e in configured:
            reason = self._skip_reason(e, snap, usable)
            board = boards.get(e.board)
            paced = board is not None and board.pace_unit is not None
            seconds: float | None = None if paced else e.duration
            items = None
            if board is not None and ctx is not None and reason is None:
                board_cfg = self._board_config(cfg, board)
                pctx = replace(ctx, pace=self._pace(board, e))
                try:
                    if seconds is None:
                        seconds = board.auto_seconds(pctx, board_cfg)
                    items = board.auto_items(pctx, board_cfg)
                except Exception:       # a board must never be able to break the dashboard
                    log.debug("rotation probe failed for board %s", e.board, exc_info=True)
            entries.append({
                "board": e.board,
                "title": board.title if board is not None else e.board,
                "duration": e.duration,
                "seconds": seconds,
                "auto": e.duration is None,
                "pace_unit": board.pace_unit if board is not None else None,
                "count": items[0] if items else None,
                "unit": items[1] if items else None,
                "skipped": reason,
                "active": False,
                "elapsed": None,
            })
        playing = [x for x in entries if x["skipped"] is None]
        index: int | None = None
        cursor = self._cursor
        if playing and cursor is not None and state in PLAYLIST_STATES and not self._active_event and not self.override:
            index = min(cursor.index, len(playing) - 1)
            playing[index]["active"] = True
            playing[index]["elapsed"] = max(0.0, mono - cursor.entered_at)
        known = [x["seconds"] for x in playing]
        event = None
        if self._active_event:
            ev, eb, started = self._active_event
            event = {"board": eb.key, "title": eb.title, "kind": ev.kind, "elapsed": max(0.0, mono - started)}
        return {
            "state": state.value,
            "board": self._active_key,
            "entries": entries,
            "index": index,
            "lap_seconds": sum(known) if playing and all(s is not None for s in known) else None,
            "event": event,
            "override": self.override,
        }

    def _select(self, cfg: AppConfig, snap: Snapshot, mono: float) -> tuple[BaseBoard, str, Event | None, PlaylistEntry | None]:
        """What to show: (board, key, event, playlist entry); the entry is None off the playlist."""
        boards = self._registry.boards
        usable = self._usable(mono)
        forced = self.override
        if forced:
            return boards[forced], forced, None, None
        if self._active_event:
            event, board, started = self._active_event
            return board, board.key, event, None
        while self._pending:
            event = self._pending.pop(0)
            for eb in self._registry.event_boards:
                if eb.key in usable and eb.matches(event, self._board_config(cfg, eb)):
                    self._active_event = (event, eb, mono)
                    return eb, eb.key, event, None
        state = self._cursor.state
        if state == AppState.BOOT:
            return self._pinned(BOOT_BOARD)
        if state == AppState.ERROR:
            return self._pinned(ERROR_BOARD)
        entries = self._entries(cfg, snap, state, usable)
        if not entries:
            return self._pinned(FALLBACK_BOARD)
        self._cursor = clamp(self._cursor, len(entries))
        entry = entries[self._cursor.index]
        return boards[entry.board], entry.board, None, entry

    def _pinned(self, key: str) -> tuple[BaseBoard, str, Event | None, None]:
        """A board the director shows by name rather than from a playlist, degrading to
        FALLBACK_BOARD and then to a blank frame if neither ever loaded."""
        boards = self._registry.boards
        board = boards.get(key) or boards.get(FALLBACK_BOARD)
        if board is not None:
            return board, key if key in boards else FALLBACK_BOARD, None, None
        return DARK_BOARD, DARK_BOARD.key, None, None

    @staticmethod
    def _pace(board: BaseBoard, entry: PlaylistEntry | None) -> float | None:
        """The playlist's seconds, reinterpreted as seconds per item for a paced board."""
        return entry.duration if entry is not None and board.pace_unit is not None else None

    def _after_render(self, board: BaseBoard, ctx: BoardContext, board_cfg: BaseModel, cfg: AppConfig, mono: float) -> None:
        if self._active_event:
            _, eb, started = self._active_event
            if eb.done(ctx, board_cfg) or mono - started > EVENT_MAX_SECONDS:
                self._active_event = None
                self._cursor = Cursor(self._cursor.state, self._cursor.index, mono)
            return
        if self._cursor.state not in PLAYLIST_STATES:
            return
        entries = self._entries(cfg, self._snapshots.get(), self._cursor.state, self._usable(mono))
        if not entries:
            return
        entry = entries[min(self._cursor.index, len(entries) - 1)]
        # A fixed number is the whole run for most boards; a paced board (ticker, flights...)
        # gets it as seconds per item through ctx.pace instead and says itself when it is done.
        expired = entry.duration is not None and board.pace_unit is None and ctx.elapsed >= entry.duration
        if expired or board.done(ctx, board_cfg):
            self._cursor = advance(self._cursor, len(entries), mono)

    def _context(self, cfg: AppConfig, snap: Snapshot, mono: float, event: Event | None, pace: float | None = None) -> BoardContext:
        # `mono` when nothing is on screen yet: the web UI asks for a context (auto_seconds)
        # before the first frame has created the cursor.
        entered = self._active_event[2] if self._active_event else (self._cursor.entered_at if self._cursor else mono)
        return BoardContext(
            snapshot=snap,
            profile=profile_for(cfg.display.width, cfg.display.height),
            width=cfg.display.width,
            height=cfg.display.height,
            fps=cfg.display.fps,
            now=self._now(cfg),
            elapsed=mono - entered,
            event=event,
            pace=pace,
        )

    def _board_config(self, cfg: AppConfig, board: BaseBoard) -> BaseModel:
        return self._board_config_impl(cfg, board)

    def _board_config_impl(self, cfg: AppConfig, board: BaseBoard) -> BaseModel:
        cache = self._board_cfg_cache
        cache_key = (board.key, self._cfg_version)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        raw = cfg.boards.get(board.key, {})
        try:
            model = board.config_model.model_validate(raw)
        except ValidationError as exc:
            log.warning("invalid config for board %s, using defaults: %s", board.key, exc)
            model = board.config_model()
        self._board_cfg_cache = {**cache, cache_key: model}
        return model


def _stale_marker(frame: Image.Image) -> Image.Image:
    """Tiny red dot bottom-right: data is being shown but the feed is unreachable."""
    out = frame.copy()
    w, h = out.size
    for dx in (1, 2):
        for dy in (1, 2):
            out.putpixel((w - 1 - dx, h - 1 - dy), STALE_DOT)
    return out
