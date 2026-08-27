"""Airline logo fetch + on-disk cache.

Square "tail" symbols keyed by operator code, from the Jxck-S/airline-logos repo — the
RadarBox set first, the FlightAware set for the codes it lacks. Wordmark logos (the sort
airline CDNs serve) are unreadable at LED scale; these are not. Unknown codes 404 in both
sets, which falls through to the board's monogram tile.

Only the *source* fetches: it stores the PNG under the cache dir and puts the path in the
aircraft dict, so boards stay pure and only ever read a local file. Drop your own art in
``assets/logos/airlines/{CODE}.png`` to override a fetched logo.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx

SOURCE_URLS = (
    "https://raw.githubusercontent.com/Jxck-S/airline-logos/main/radarbox_logos/{code}.png",
    "https://raw.githubusercontent.com/Jxck-S/airline-logos/main/flightaware_logos/{code}.png",
)
BUNDLED_DIR = Path(__file__).resolve().parents[2] / "assets" / "logos" / "airlines"
CACHE_ROOT = Path(os.environ.get("SCOREBOARD_CACHE_DIR") or Path.home() / ".scoreboard" / "cache")
CACHE_DIR = CACHE_ROOT / "airline-logos"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_LOGO_BYTES = 256 * 1024
MISS_TTL = 7 * 86400        # absent from every set: a genuine miss, don't re-ask this week
ERROR_TTL = 3600            # network/HTTP failure: retry hourly
FETCH_TIMEOUT = 10.0


def safe_code(code: str | None) -> str:
    """Operator codes reach us from external APIs and end up in a URL and a filename."""
    code = (code or "").strip().upper()
    return code if 2 <= len(code) <= 4 and code.isalnum() and code.isascii() else ""


def codes_for(aircraft: dict[str, Any]) -> tuple[str, ...]:
    """Operator codes to try, best first: ICAO, IATA, then the callsign's airline prefix."""
    callsign = (aircraft.get("callsign") or "").strip().upper()
    prefix = callsign[:3] if len(callsign) > 3 and callsign[:3].isalpha() else ""
    candidates = (aircraft.get("airline_icao"), aircraft.get("airline_iata"), prefix)
    return tuple(dict.fromkeys(c for c in map(safe_code, candidates) if c))


class LogoFetcher:
    """Resolves an aircraft to a local PNG path, downloading once per operator code."""

    def __init__(self, cache_dir: Path | None = None, bundled_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir or CACHE_DIR
        self._bundled_dir = bundled_dir or BUNDLED_DIR
        self._misses: dict[str, float] = {}   # code -> retry-after timestamp

    def local_path(self, code: str) -> Path | None:
        """A user override or an already-cached logo, without touching the network."""
        for path in (self._bundled_dir / f"{code}.png", self._cache_dir / f"{code}.png"):
            if path.is_file():
                return path
        return None

    async def path_for(self, http: httpx.AsyncClient, aircraft: dict[str, Any], log) -> str:
        for code in codes_for(aircraft):
            path = self.local_path(code) or await self._fetch(http, code, log)
            if path is not None:
                return str(path)
        return ""

    async def _fetch(self, http: httpx.AsyncClient, code: str, log) -> Path | None:
        if self._misses.get(code, 0.0) > time.time():
            return None
        content = None
        for url in SOURCE_URLS:
            try:
                resp = await http.get(url.format(code=code), timeout=FETCH_TIMEOUT, follow_redirects=True)
            except httpx.HTTPError as exc:
                log.warning("airline logo fetch failed for %s: %s", code, exc)
                return self._miss(code, ERROR_TTL)
            if resp.status_code == 404:
                continue                       # not in this set; try the next one
            if resp.status_code >= 400:
                log.warning("airline logo fetch for %s returned HTTP %s", code, resp.status_code)
                return self._miss(code, ERROR_TTL)
            content = resp.content
            break
        if not content or not content.startswith(PNG_MAGIC) or len(content) > MAX_LOGO_BYTES:
            if content:
                log.warning("airline logo for %s was not a usable PNG (%d bytes)", code, len(content))
            return self._miss(code, MISS_TTL)
        return self._store(code, content, log)

    def _miss(self, code: str, ttl: float) -> None:
        self._misses[code] = time.time() + ttl
        return None

    def _store(self, code: str, content: bytes, log) -> Path | None:
        path = self._cache_dir / f"{code}.png"
        tmp = path.with_suffix(".png.tmp")
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(content)
            tmp.replace(path)                  # atomic: boards never see a half-written file
        except OSError as exc:
            log.warning("could not cache airline logo %s: %s", code, exc)
            tmp.unlink(missing_ok=True)
            return self._miss(code, ERROR_TTL)
        log.info("fetched airline logo for %s (%d bytes)", code, len(content))
        return path
