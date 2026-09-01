"""Endpoints behind the Holidays panel: the pictures, and the settings that need
more than ``/api/config`` can give.

``/images/{slug}`` is one path with three methods: read the picture for a slug (uploaded,
else the one we ship), put your own there, or take yours away again. The slug is a
filename stem, so every route checks it before it is allowed anywhere near the
filesystem — see ``extras/holidays/images.py``.

``/settings`` exists because the generic config API cannot express what this panel does.
``PATCH /api/config`` deep-merges, so a key can be added to ``overrides`` but never taken
out of it, and plugin sections are typed ``dict[str, Any]`` in ``AppConfig``, so nothing
validates them on the way in — a typo is accepted, persisted, and only discovered when
the source next reads it. This route validates against ``HolidaysConfig`` and replaces
the section outright, so turning a holiday back to its default really does remove it.

These live in the web layer rather than in the plugin so that ``extras/`` stays free of
FastAPI, and they hold no logic of their own: validation and storage belong to the
plugin, and this only turns its errors into status codes.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError

from ..config import ConfigStore
from ..data import SnapshotStore
from ..extras.holidays import images, source

PAYLOAD_TOO_LARGE = 413
UNPROCESSABLE = 422


def _validated(raw: object, strict: bool = False) -> source.HolidaysConfig:
    """Parse a holidays section. Invalid stored config falls back to defaults the way the
    source does; invalid *incoming* config is refused, so a typo cannot be saved."""
    try:
        return source.HolidaysConfig.model_validate(raw)
    except ValidationError as exc:
        if strict:
            raise HTTPException(status_code=UNPROCESSABLE, detail=exc.errors()) from exc
        return source.HolidaysConfig()


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
    api = APIRouter(prefix="/api/holidays", tags=["holidays"])
    # Editing the settings has to reach the panel and the panel's board at once, not an
    # hour later when the source next wakes. The listener does that for config changes;
    # the picture routes below call refresh() directly, since a file is not config.
    config.subscribe(source.config_listener(snapshots))

    def refresh() -> None:
        """The board draws the path the snapshot carries, so a new picture is invisible
        until the source next recomputes — an hour away. This closes that gap."""
        source.refresh(config.get(), snapshots)

    @api.get("/settings")
    def read_settings() -> dict[str, object]:
        """The holiday settings as the source sees them: stored values over model defaults."""
        return _validated(config.get().sources.get(source.KEY, {})).model_dump(mode="json")

    @api.put("/settings")
    def write_settings(body: dict[str, object]) -> dict[str, object]:
        """Replace the whole holidays section — the only way to *remove* an override."""
        settings = _validated(body, strict=True).model_dump(mode="json")
        document = config.get().model_dump(mode="json")
        document["sources"] = {**document["sources"], source.KEY: settings}
        config.replace(document)
        return settings

    @api.get("/images/{slug}")
    def read(slug: str) -> FileResponse:
        path = images.resolve(slug)
        if path is None:
            raise HTTPException(status_code=404, detail=f"no picture for {slug!r}")
        # A picture can be replaced in place, so the browser must ask before reusing it.
        # FileResponse still sends an ETag, which makes that a cheap 304.
        return FileResponse(path, media_type="image/png", headers={"cache-control": "no-cache"})

    @api.post("/images/{slug}")
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

    @api.delete("/images/{slug}")
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
