"""Endpoints for the pictures shown on the holiday countdown board.

One path, three methods: read the picture for a slug (uploaded, else the one we ship),
put your own there, or take yours away again. The slug is a filename stem, so every
route checks it before it is allowed anywhere near the filesystem — see
``extras/holidays/images.py``.

These live in the web layer rather than in the plugin so that ``extras/`` stays free of
FastAPI, and they hold no logic of their own: validation and storage belong to the
plugin, and this only turns its errors into status codes.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..config import ConfigStore
from ..data import SnapshotStore
from ..extras.holidays import images, source

PAYLOAD_TOO_LARGE = 413
UNPROCESSABLE = 422


async def _read_capped(request: Request) -> bytes:
    """The body, refusing anything over the limit as it arrives.

    Streamed rather than ``await request.body()`` so an oversized POST is stopped at the
    cap instead of being buffered whole and only then measured.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > images.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=PAYLOAD_TOO_LARGE,
                                detail=f"pictures must be under {images.MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        chunks.append(chunk)
    return b"".join(chunks)


def router(config: ConfigStore, snapshots: SnapshotStore) -> APIRouter:
    api = APIRouter(prefix="/api/holidays/images", tags=["holidays"])

    def refresh() -> None:
        """The board draws the path the snapshot carries, so a new picture is invisible
        until the source next recomputes — an hour away. This closes that gap."""
        source.refresh(config.get(), snapshots)

    @api.get("/{slug}")
    def read(slug: str) -> FileResponse:
        path = images.resolve(slug)
        if path is None:
            raise HTTPException(status_code=404, detail=f"no picture for {slug!r}")
        # A picture can be replaced in place, so the browser must ask before reusing it.
        # FileResponse still sends an ETag, which makes that a cheap 304.
        return FileResponse(path, media_type="image/png", headers={"cache-control": "no-cache"})

    @api.post("/{slug}")
    async def write(slug: str, request: Request) -> dict[str, object]:
        """The picture is the whole request body — no form encoding, so no extra
        dependency and the browser can hand us a File object directly."""
        try:
            images.check_slug(slug)                  # a bad name is a bad name at any size
        except images.ImageError as exc:
            raise HTTPException(status_code=UNPROCESSABLE, detail=str(exc)) from exc
        data = await _read_capped(request)
        try:
            path = images.save(slug, data)
        except images.ImageError as exc:
            raise HTTPException(status_code=UNPROCESSABLE, detail=str(exc)) from exc
        refresh()
        return {"slug": slug, "bytes": path.stat().st_size}

    @api.delete("/{slug}")
    def clear(slug: str) -> dict[str, object]:
        try:
            removed = images.remove(slug)
        except images.ImageError as exc:
            raise HTTPException(status_code=UNPROCESSABLE, detail=str(exc)) from exc
        if removed:
            refresh()
        # Deleting an upload can reveal a bundled picture underneath it.
        return {"slug": slug, "removed": removed, "falls_back_to_bundled": images.resolve(slug) is not None}

    return api
