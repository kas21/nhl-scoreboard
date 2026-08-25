"""HTTP + WebSocket API. Everything the browser UI needs lives here."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .. import __version__
from ..config import ConfigStore
from ..config.schema import app_schema
from ..data import SnapshotStore
from ..director import Director
from ..output import PreviewHub
from ..plugins import Registry

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class LogBuffer(logging.Handler):
    """Keeps the last N log lines for the diagnostics page."""

    def __init__(self, size: int = 300) -> None:
        super().__init__()
        self.lines: deque[str] = deque(maxlen=size)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def create_app(
    config: ConfigStore,
    snapshots: SnapshotStore,
    registry: Registry,
    director: Director,
    preview: PreviewHub,
    logs: LogBuffer | None = None,
) -> FastAPI:
    app = FastAPI(title="scoreboard", version=__version__)

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        snap = snapshots.get()
        return {
            "version": __version__,
            "state": director.state.value,
            "board": director.active_board,
            "brightness": director.brightness(),
            "snapshot_version": snap.version,
            "sources": {k: snap.age(k) for k in snap.data},
            "setup_complete": config.get().setup_complete,
        }

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return config.get().model_dump(mode="json")

    @app.patch("/api/config")
    def patch_config(patch: dict[str, Any]) -> dict[str, Any]:
        try:
            return config.update(patch).model_dump(mode="json")
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

    @app.put("/api/config")
    def put_config(document: dict[str, Any]) -> dict[str, Any]:
        try:
            return config.replace(document).model_dump(mode="json")
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

    @app.post("/api/config/reset")
    def reset_config() -> dict[str, Any]:
        return config.reset().model_dump(mode="json")

    @app.get("/api/schema")
    def schema() -> dict[str, Any]:
        return app_schema(registry.board_models(), registry.source_models())

    @app.get("/api/boards")
    def boards() -> list[dict[str, Any]]:
        return [
            {"key": b.key, "title": b.title, "requires": sorted(b.requires), "event": hasattr(b, "event_kinds")}
            for b in registry.boards.values()
        ]

    @app.get("/api/snapshot")
    def snapshot() -> dict[str, Any]:
        snap = snapshots.get()
        return {"version": snap.version, "data": dict(snap.data)}

    @app.get("/api/logs")
    def get_logs() -> list[str]:
        return list(logs.lines) if logs else []

    @app.get("/api/preview.png")
    def preview_png() -> Response:
        data = preview.latest()
        if data is None:
            raise HTTPException(status_code=404, detail="no frame yet")
        return Response(content=data, media_type="image/png")

    @app.websocket("/ws/preview")
    async def preview_ws(ws: WebSocket) -> None:
        await ws.accept()
        queue = preview.subscribe()
        try:
            latest = preview.latest()
            if latest:
                await ws.send_bytes(latest)
            while True:
                data = await asyncio.wait_for(queue.get(), timeout=30)
                await ws.send_bytes(data)
        except (TimeoutError, WebSocketDisconnect):
            pass
        finally:
            preview.unsubscribe(queue)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
