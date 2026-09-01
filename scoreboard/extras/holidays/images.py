"""Where holiday pictures live, how a holiday name finds one, and how you add your own.

Two directories, checked in this order:

* ``USER_IMAGES`` — what you uploaded. Under ``$SCOREBOARD_DATA_DIR``, deliberately
  outside the git checkout, because an OTA update fast-forwards the working tree and
  would take anything stored there with it.
* ``IMAGES`` — the artwork the project ships.

So dropping in ``christmas_day.png`` replaces the shipped Christmas picture, and
deleting it puts the original back — nothing here ever writes to the bundled set.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from io import BytesIO
from pathlib import Path

from PIL import Image

from ...imagecache import DATA_ROOT, store

log = logging.getLogger(__name__)

IMAGES = Path(__file__).parent.parent.parent / "assets" / "holidays"
USER_IMAGES = DATA_ROOT / "holidays"

# A slug is used unescaped as a filename, so this is the whole defence against a name
# reaching outside USER_IMAGES. Anything else is refused rather than sanitised: quietly
# rewriting a name would leave the caller pointing at a file it did not ask for.
SLUG = re.compile(r"^[a-z0-9_]{1,64}$")

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
# Checked against the header before a single row is decoded, so a small file claiming
# enormous dimensions costs nothing.
MAX_SOURCE_PIXELS = 40_000_000
# Panels are at most 64 rows tall today; 256 leaves room and still keeps files small.
STORED_SIZE = 256
READABLE_FORMATS = frozenset({"PNG", "JPEG", "GIF", "WEBP", "BMP"})

_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*$")


class ImageError(ValueError):
    """Something was wrong with an upload. The message is written to be shown to the user."""


def slug(name: str) -> str:
    """Filename stem for a holiday name.

    Apostrophes are dropped rather than treated as separators: they used to split the
    word, so ``New Year's Day`` looked for ``new_year_s_day.png`` and never found the
    ``new_years_day.png`` we ship. Accents fold to their base letter for the same
    reason, so a French calendar does not ask for ``f_te_du_canada.png``.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", folded.lower().replace("'", "")).strip("_")


def base_name(name: str) -> str:
    """``Independence Day (observed)`` -> ``Independence Day``, so it borrows the same picture."""
    return _PARENTHETICAL.sub("", name).strip() or name


def check_slug(value: str) -> str:
    if not SLUG.fullmatch(value):
        raise ImageError("a picture name may only be lowercase letters, digits and underscores")
    return value


def stored_path(value: str) -> Path:
    """Where an upload for ``value`` goes. Raises rather than return a path outside the dir."""
    return USER_IMAGES / f"{check_slug(value)}.png"


def resolve(value: str) -> Path | None:
    """The picture for one slug — uploaded first, then bundled — or None if we have neither."""
    if not SLUG.fullmatch(value):
        return None
    for root in (USER_IMAGES, IMAGES):
        path = root / f"{value}.png"
        if path.exists():
            return path
    return None


def image_path(name: str, explicit: str = "") -> str | None:
    """The picture for a holiday, as an absolute path, or None if we have none.

    An explicit slug wins, then the holiday's own name, then the name with any
    ``(observed)`` suffix removed.
    """
    for stem in dict.fromkeys(s for s in (explicit, slug(name), slug(base_name(name))) if s):
        found = resolve(stem)
        if found is not None:
            return str(found)
    return None


def uploaded(value: str) -> bool:
    """True if an upload is standing at this slug — so a delete would actually do something."""
    return bool(SLUG.fullmatch(value)) and (USER_IMAGES / f"{value}.png").exists()


def save(value: str, data: bytes) -> Path:
    """Validate an upload and store it as a normalised PNG. Raises :class:`ImageError`.

    The uploaded bytes are never stored as they arrived: whatever comes in is decoded,
    downscaled and re-encoded, so what lands on disk is something we wrote.
    """
    path = stored_path(value)
    if not data:
        raise ImageError("the upload was empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageError(f"pictures must be under {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        with Image.open(BytesIO(data)) as probe:
            if probe.format not in READABLE_FORMATS:
                raise ImageError(f"{probe.format or 'that file'} is not a picture format we can read")
            width, height = probe.size
            if width * height > MAX_SOURCE_PIXELS:
                raise ImageError("that picture has too many pixels")
            picture = probe.convert("RGBA")
    except ImageError:
        raise
    except Exception as exc:                       # Pillow raises a wide spread on bad input
        raise ImageError("that file is not a picture we can read") from exc
    picture.thumbnail((STORED_SIZE, STORED_SIZE), Image.LANCZOS)
    buffer = BytesIO()
    picture.save(buffer, "PNG", optimize=True)
    if not store(path, buffer.getvalue(), log):
        raise ImageError(f"could not write to {path.parent}")
    return path


def remove(value: str) -> bool:
    """Delete an uploaded picture, putting any bundled one back. True if one was there."""
    path = stored_path(value)
    if not path.exists():
        return False
    path.unlink()
    return True
