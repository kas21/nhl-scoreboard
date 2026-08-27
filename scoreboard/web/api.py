"""HTTP + WebSocket API. Everything the browser UI needs lives here."""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import socket
import subprocess
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
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
from .updater import Updater

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class SystemControl:
    """Host-level actions the UI may trigger. Each is best-effort and logs what it did."""

    def __init__(self, restart: Callable[[], None] | None = None) -> None:
        self._restart = restart

    def restart(self) -> bool:
        if self._restart is None:
            return False
        self._restart()
        return True

    @staticmethod
    def hostname() -> str:
        return socket.gethostname()

    @staticmethod
    def set_hostname(name: str) -> bool:
        """Set the machine hostname (so ``name.local`` resolves). Needs hostnamectl + root."""
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", name):
            raise ValueError("hostname must be lowercase letters, digits and dashes")
        if shutil.which("hostnamectl") is None:
            return False
        subprocess.run(["hostnamectl", "set-hostname", name], check=True, timeout=10)
        return True

    @staticmethod
    def version_info() -> dict[str, Any]:
        root = Path(__file__).resolve().parents[2]
        rev = None
        if shutil.which("git") and (root / ".git").exists():
            try:
                rev = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or None
            except (subprocess.SubprocessError, OSError):
                rev = None
        return {"version": __version__, "git": rev}


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
    system: SystemControl | None = None,
    updater: Updater | None = None,
) -> FastAPI:
    app = FastAPI(title="scoreboard", version=__version__)
    system = system or SystemControl()
    updater = updater or Updater(restart=system._restart)

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

    def effective(cfg) -> dict[str, Any]:
        """Config as the app sees it: plugin sections filled in with each model's defaults.

        config.json stores only overrides, so without this the UI would show
        defaults as blank / unchecked.
        """
        doc = cfg.model_dump(mode="json")
        for section, models in (("boards", registry.board_models()), ("sources", registry.source_models())):
            merged = {}
            for key, model in models.items():
                raw = doc[section].get(key, {})
                try:
                    merged[key] = model.model_validate(raw).model_dump(mode="json")
                except ValidationError:
                    merged[key] = {**model().model_dump(mode="json"), **raw}
            doc[section] = {**doc[section], **merged}
        return doc

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return effective(config.get())

    @app.patch("/api/config")
    def patch_config(patch: dict[str, Any]) -> dict[str, Any]:
        try:
            return effective(config.update(patch))
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

    @app.put("/api/config")
    def put_config(document: dict[str, Any]) -> dict[str, Any]:
        try:
            return effective(config.replace(document))
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

    @app.post("/api/config/reset")
    def reset_config() -> dict[str, Any]:
        return effective(config.reset())

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

    @app.post("/api/override")
    def set_override(body: dict[str, Any]) -> dict[str, Any]:
        """Force a board onto the display (wizard test pattern, board previews)."""
        board = body.get("board")
        if board is not None and board not in registry.boards:
            raise HTTPException(status_code=404, detail=f"unknown board {board!r}")
        director.set_override(board, float(body.get("seconds", 60)))
        return {"override": director.override}

    @app.get("/api/system")
    def system_info() -> dict[str, Any]:
        return {**system.version_info(), "hostname": system.hostname(), "can_restart": system._restart is not None}

    @app.get("/api/system/update")
    def update_state() -> dict[str, Any]:
        return updater.state()

    @app.post("/api/system/update/check")
    def update_check() -> dict[str, Any]:
        return updater.check()

    @app.post("/api/system/update")
    def update_start() -> dict[str, Any]:
        return {"started": updater.update(), **updater.state()}

    @app.post("/api/system/restart")
    def system_restart() -> dict[str, Any]:
        return {"restarting": system.restart()}

    @app.post("/api/system/hostname")
    def system_hostname(body: dict[str, Any]) -> dict[str, Any]:
        try:
            ok = system.set_hostname(str(body.get("hostname", "")))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (subprocess.SubprocessError, OSError) as exc:
            raise HTTPException(status_code=500, detail=f"could not set hostname: {exc}") from exc
        return {"hostname": system.hostname(), "changed": ok}

    @app.get("/api/geocode")
    async def geocode(q: str) -> list[dict[str, Any]]:
        """Town / postcode -> candidates with lat, lon and timezone (Open-Meteo geocoder, keyless)."""
        q = q.strip()
        if len(q) < 2:
            return []
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": q, "count": 6, "language": "en", "format": "json"})
                r.raise_for_status()
                results = r.json().get("results") or []
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=f"geocoding failed: {exc}") from exc
        return [{"name": x.get("name"), "region": x.get("admin1", ""), "country": x.get("country_code", ""),
                 "latitude": round(x["latitude"], 3), "longitude": round(x["longitude"], 3), "timezone": x.get("timezone", "")}
                for x in results if "latitude" in x and "longitude" in x]

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
