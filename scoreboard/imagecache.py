"""On-disk cache for images the app downloads at runtime.

Team logos and airline logos are fetched once and stored here rather than shipped in
the repo, so the project never redistributes artwork it does not own. Sources do the
downloading; boards only ever read what has already landed, keeping them pure.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PIL import Image

CACHE_ROOT = Path(os.environ.get("SCOREBOARD_CACHE_DIR") or Path.home() / ".scoreboard" / "cache")
# Files the *user* put here, which nothing can re-download: uploaded holiday pictures
# and the like. Separate from CACHE_ROOT because clearing a cache must stay safe.
DATA_ROOT = Path(os.environ.get("SCOREBOARD_DATA_DIR") or Path.home() / ".scoreboard" / "data")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 1024 * 1024


def is_png(content: bytes) -> bool:
    """Never trust a CDN response: a redirect to an error page is still HTTP 200."""
    return bool(content) and content.startswith(PNG_MAGIC) and len(content) <= MAX_PNG_BYTES


def store(path: Path, content: bytes, log) -> bool:
    """Write atomically so a reader never sees a half-downloaded file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(content)
        tmp.replace(path)
    except OSError as exc:
        log.warning("could not cache %s: %s", path.name, exc)
        tmp.unlink(missing_ok=True)
        return False
    return True


def load(path: Path, size: int) -> Image.Image | None:
    """Cached RGBA thumbnail that fits a ``size`` square, or None if nothing is cached yet.

    Keyed on the file's mtime, so a logo that arrives after a miss is picked up rather
    than the miss being cached for the life of the process.
    """
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    return _decode(str(path), size, mtime)


@lru_cache(maxsize=256)
def _decode(path: str, size: int, mtime: int) -> Image.Image | None:
    try:
        with Image.open(path) as src:
            img = src.convert("RGBA")
    except (OSError, ValueError):
        return None
    img.thumbnail((size, size), Image.LANCZOS)
    return img
