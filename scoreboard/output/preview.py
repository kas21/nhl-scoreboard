"""Fan the latest frame out to browser preview clients as PNG bytes.

Encoding happens on a worker thread so the render loop only pays for a frame copy;
frames are dropped (never queued up) when the encoder or the client falls behind.
Nothing is encoded while no browser is connected.
"""
from __future__ import annotations

import asyncio
import io
import queue
import threading
import time

from PIL import Image


class PreviewHub:
    def __init__(self, fps: int = 30) -> None:
        self._interval = 1.0 / max(fps, 1)
        self._last_submit = 0.0
        self._latest: bytes | None = None
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue[bytes]] = set()
        self._pending: queue.Queue[Image.Image] = queue.Queue(maxsize=1)
        self._worker = threading.Thread(target=self._encode_loop, name="preview-encode", daemon=True)
        self._worker.start()

    def set_fps(self, fps: int) -> None:
        self._interval = 1.0 / max(fps, 1)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def watching(self) -> bool:
        return bool(self._subscribers)

    def submit(self, frame: Image.Image) -> None:
        """Called from the render thread. Cheap: copies the frame and hands it to the encoder (drop if busy)."""
        now = time.monotonic()
        if now - self._last_submit < self._interval:
            return
        # keep one frame around for /api/preview.png even with no watchers, but only encode ~1/s then
        if not self._subscribers and now - self._last_submit < 1.0:
            return
        self._last_submit = now
        try:
            self._pending.put_nowait(frame.copy())
        except queue.Full:
            pass                                    # encoder busy: drop this frame

    def _encode_loop(self) -> None:
        while True:
            frame = self._pending.get()
            buf = io.BytesIO()
            frame.save(buf, format="PNG", compress_level=1)
            data = buf.getvalue()
            with self._lock:
                self._latest = data
                subs = list(self._subscribers)
            if self._loop and subs:
                self._loop.call_soon_threadsafe(self._broadcast, data, subs)

    def _broadcast(self, data: bytes, subs: list[asyncio.Queue[bytes]]) -> None:
        for q in subs:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(data)

    def latest(self) -> bytes | None:
        with self._lock:
            return self._latest

    def subscribe(self) -> asyncio.Queue[bytes]:
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[bytes]) -> None:
        with self._lock:
            self._subscribers.discard(q)
