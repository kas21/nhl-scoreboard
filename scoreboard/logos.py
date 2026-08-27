"""Team logos, fetched once from ESPN's CDN and cached on disk.

The repo ships no club artwork. A source calls :func:`prefetch` on startup to pull the
league's logos in the background; boards call :func:`logo`, which only reads the cache
and returns None until the file has landed (callers draw their own placeholder).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from PIL import Image

from .imagecache import CACHE_ROOT, is_png, load, store

CDN = "https://a.espncdn.com/i/teamlogos/{sport}/500/{code}.png"
LOGO_DIR = CACHE_ROOT / "logos"
CONCURRENCY = 4
FETCH_TIMEOUT = 20.0
# ESPN's path segment is the lowercased abbreviation, bar a handful of legacy short codes
ESPN_CODES: dict[str, dict[str, str]] = {"nhl": {"LAK": "la", "SJS": "sj", "TBL": "tb"}}


def espn_code(sport: str, abbrev: str) -> str:
    return ESPN_CODES.get(sport, {}).get(abbrev.upper(), abbrev.lower())


def path(sport: str, abbrev: str) -> Path:
    return LOGO_DIR / sport / f"{abbrev.upper()}.png"


def logo(sport: str, abbrev: str, size: int) -> Image.Image | None:
    """The cached logo scaled to fit a ``size`` square, or None if it hasn't been fetched."""
    return load(path(sport, abbrev), size)


async def prefetch(http: httpx.AsyncClient, sport: str, abbrevs: tuple[str, ...], log) -> int:
    """Download whatever this league is missing. Safe to call on every start: it's a no-op once cached."""
    missing = [a for a in abbrevs if not path(sport, a).is_file()]
    if not missing:
        return 0
    log.info("fetching %d %s team logos (one time, then cached in %s)", len(missing), sport, LOGO_DIR / sport)
    limit = asyncio.Semaphore(CONCURRENCY)

    async def one(abbrev: str) -> bool:
        async with limit:
            return await _fetch(http, sport, abbrev, log)

    got = sum(await asyncio.gather(*(one(a) for a in missing)))
    log.info("cached %d/%d %s logos", got, len(missing), sport)
    return got


async def _fetch(http: httpx.AsyncClient, sport: str, abbrev: str, log) -> bool:
    url = CDN.format(sport=sport, code=espn_code(sport, abbrev))
    try:
        resp = await http.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("logo fetch failed for %s %s: %s", sport, abbrev, exc)
        return False
    if not is_png(resp.content):
        log.warning("logo for %s %s was not a usable PNG (%d bytes)", sport, abbrev, len(resp.content))
        return False
    return store(path(sport, abbrev), resp.content, log)
