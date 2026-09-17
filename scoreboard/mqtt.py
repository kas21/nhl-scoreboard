"""MQTT bridge: what the panel knows goes out to a broker, a few commands come back.

Home Assistant (or anything else on the broker) can then show the score, the state of
the panel and the goals as they happen, and switch the panel off or force a board.

Topics, under ``mqtt.topic_prefix`` (``scoreboard`` by default)::

    status                 online / offline (retained; offline is the broker's last will)
    state                  {state, board, brightness, override}   retained, on change
    snapshot/<key>         the snapshot value, dots as slashes   retained, on change
                           e.g. snapshot/main_event, snapshot/nhl/scores, snapshot/weather/current
    event/<kind>           {kind, team, payload, ts}              e.g. event/nhl/goal
    cmd/board       <-     a board key, or {"board": key, "seconds": n}; empty / "none" clears
    cmd/power       <-     off (blank the panel until told otherwise) / on

The bridge is fed by the same two hooks everything else uses — a snapshot listener and an
event-bus tap — and those run on whichever thread published, so it only marks work here
and lets its own asyncio task do the talking. Everything is republished on (re)connect,
retained, so a subscriber that comes late still sees the current picture.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import threading
from collections.abc import Callable
from typing import Any

from .config.models import MqttConfig
from .data.events import Event, EventBus
from .data.store import Snapshot, SnapshotStore

log = logging.getLogger(__name__)

IDLE_RECHECK_SECONDS = 3            # how often a disabled bridge looks at its config again
STATE_INTERVAL = 1.0                # how often the panel state is compared and published on change
RECONNECT_DELAYS = (2, 5, 15, 30, 60)
POWER_OFF_BOARD = "blank"
POWER_OFF_SECONDS = 365 * 24 * 3600.0   # "until told otherwise": an override needs a length


class MqttBridge:
    def __init__(
        self,
        config_getter: Callable[[], MqttConfig],
        snapshots: SnapshotStore,
        events: EventBus,
        director: Any,
        *,
        client_factory: Callable[[MqttConfig, str], Any] | None = None,
    ) -> None:
        self._config = config_getter
        self._snapshots = snapshots
        self._director = director
        self._factory = client_factory or _aiomqtt_client
        self._lock = threading.Lock()
        self._dirty: set[str] = set()
        self._pending: list[Event] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._last_state: dict[str, Any] | None = None
        self.connected = False
        self.error: str | None = None
        snapshots.subscribe(self._on_snapshot)
        events.subscribe(self._on_events)

    # -- feeds (any thread) --------------------------------------------------

    def _on_snapshot(self, prev: Snapshot, new: Snapshot) -> None:
        changed = [k for k in new.data if new.versions.get(k) != prev.versions.get(k)]
        if not changed:
            return
        with self._lock:
            self._dirty.update(changed)
        self._poke()

    def _on_events(self, events: list[Event]) -> None:
        with self._lock:
            self._pending.extend(events)
        self._poke()

    def _poke(self) -> None:
        if self._loop is not None and self._wake is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    # -- status ----------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        cfg = self._config()
        return {"enabled": cfg.enabled and bool(cfg.host.strip()), "connected": self.connected,
                "host": cfg.host, "port": cfg.port, "prefix": cfg.topic_prefix.strip("/"), "error": self.error}

    # -- the task --------------------------------------------------------------

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        failures = 0
        while True:
            cfg = self._config()
            if not cfg.enabled or not cfg.host.strip():
                self.error = None
                await asyncio.sleep(IDLE_RECHECK_SECONDS)
                continue
            try:
                await self._session(cfg)
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                self.error = f"{type(exc).__name__}: {exc}"
                delay = RECONNECT_DELAYS[min(failures - 1, len(RECONNECT_DELAYS) - 1)]
                log.warning("MQTT %s:%s: %s; retrying in %ss", cfg.host, cfg.port, self.error, delay)
                await asyncio.sleep(delay)

    async def _session(self, cfg: MqttConfig) -> None:
        """One connection; returns when the settings change so the caller reconnects with the new ones."""
        prefix = cfg.topic_prefix.strip("/")
        async with self._factory(cfg, f"{prefix}/status") as client:
            self.connected, self.error = True, None
            log.info("MQTT connected to %s:%s, publishing under %s/", cfg.host, cfg.port, prefix)
            with self._lock:                                # start from the whole picture, retained
                self._dirty = set(self._snapshots.get().data)
                self._pending = []
            self._last_state = None
            await client.publish(f"{prefix}/status", "online", retain=True)
            await client.subscribe(f"{prefix}/cmd/#")
            inbound = asyncio.create_task(self._inbound(client, prefix), name="mqtt-inbound")
            try:
                while True:
                    await self._flush(client, cfg, prefix)
                    if inbound.done():
                        inbound.result()                    # the connection dropped: raise into run()
                    if self._config() != cfg:
                        log.info("MQTT settings changed; reconnecting")
                        return
                    assert self._wake is not None
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), timeout=STATE_INTERVAL)
                    self._wake.clear()
            finally:
                self.connected = False
                inbound.cancel()
                with contextlib.suppress(BaseException):
                    await inbound

    async def _flush(self, client: Any, cfg: MqttConfig, prefix: str) -> None:
        with self._lock:
            dirty, self._dirty = self._dirty, set()
            events, self._pending = self._pending, []
        snap = self._snapshots.get()
        for key in sorted(dirty):
            if key in snap.data and wanted(cfg, key):
                await client.publish(f"{prefix}/snapshot/{key.replace('.', '/')}", _dumps(snap.data[key]), retain=True)
        for ev in events:
            payload = {"kind": ev.kind, "team": ev.team, "payload": ev.payload, "ts": ev.ts}
            await client.publish(f"{prefix}/event/{ev.kind.replace('.', '/')}", _dumps(payload))
        state = self._state()
        if state != self._last_state:
            await client.publish(f"{prefix}/state", _dumps(state), retain=True)
            self._last_state = state

    def _state(self) -> dict[str, Any]:
        d = self._director
        return {"state": d.state.value, "board": d.active_board, "brightness": d.brightness(), "override": d.override}

    async def _inbound(self, client: Any, prefix: str) -> None:
        async for message in client.messages:
            topic = getattr(message.topic, "value", None) or str(message.topic)
            payload = message.payload
            text = payload.decode("utf-8", "replace") if isinstance(payload, bytes | bytearray) else str(payload or "")
            try:
                self.command(topic[len(prefix) + 1:], text.strip())
            except Exception:
                log.exception("MQTT command %s failed", topic)

    # -- commands --------------------------------------------------------------

    def command(self, topic: str, text: str) -> None:
        """Act on one inbound message; ``topic`` is relative to the prefix (``cmd/board``)."""
        if topic == "cmd/board":
            board, seconds = parse_board_command(text)
            log.info("MQTT: %s", f"show board {board!r} for {seconds:g}s" if board else "clear board override")
            self._director.set_override(board, seconds)
        elif topic == "cmd/power":
            if text.lower() in ("off", "0", "false"):
                log.info("MQTT: panel off")
                self._director.set_override(POWER_OFF_BOARD, POWER_OFF_SECONDS)
            elif text.lower() in ("on", "1", "true"):
                log.info("MQTT: panel on")
                self._director.set_override(None)
            else:
                log.warning("MQTT: cmd/power wants on or off, not %r", text)
        else:
            log.debug("MQTT: ignoring %s", topic)


def parse_board_command(text: str) -> tuple[str | None, float]:
    """``cmd/board`` payloads: a bare board key, ``{"board": key, "seconds": n}``, or empty to clear."""
    if not text or text.lower() in ("none", "null", "clear"):
        return None, 0.0
    if text.startswith("{"):
        body = json.loads(text)
        board = body.get("board")
        return (str(board) if board else None), float(body.get("seconds", 60))
    return text, 60.0


def wanted(cfg: MqttConfig, key: str) -> bool:
    prefixes = [p.strip() for p in cfg.snapshot_keys if p.strip()]
    return not prefixes or any(key == p or key.startswith(p + ".") for p in prefixes)


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _aiomqtt_client(cfg: MqttConfig, will_topic: str) -> Any:
    import aiomqtt

    return aiomqtt.Client(
        cfg.host.strip(), cfg.port,
        username=cfg.username or None, password=cfg.password or None,
        identifier=f"scoreboard-{socket.gethostname()}",
        will=aiomqtt.Will(will_topic, "offline", retain=True),
        keepalive=30, timeout=10,
    )
