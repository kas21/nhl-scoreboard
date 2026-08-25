"""Fan the latest frame out to browser preview clients as PNG bytes."""
from __future__ import annotations

import asyncio
import io
import threading
import time

from PIL import Image


class PreviewHub:
    def __init__(self, fps: int = 10) -> None:
        self._interval = 1.0 / fps
        self._last_sent = 0.0
        self._latest: bytes | None = None
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue[bytes]] = set()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def submit(self, frame: Image.Image) -> None:
        """Called from the render thread; throttled and cheap when nobody is watching."""
        now = time.monotonic()
        if now - self._last_sent < self._interval:
            return
        self._last_sent = now
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
