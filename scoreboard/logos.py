"""Team logos, fetched once from ESPN's CDN and cached on disk.

The repo ships no club artwork. A source calls :func:`watch` on startup to pull the
league's logos in the background; boards call :func:`logo`, which only reads the cache
and returns None until the file has landed (callers draw their own placeholder).

Each team can use one of several :mod:`~scoreboard.logovariants` — the primary mark in
club colours by default, or a lighter treatment / the secondary mark for the clubs whose
default is unreadable on a dark panel. Boards never pass a variant: the choice comes from
config, is applied with :func:`apply_config`, and resolves inside :func:`logo`, so a
preference change needs no board change and no restart.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

import httpx
from PIL import Image

from .espn import HEADERS as ESPN_HEADERS
from .imagecache import CACHE_ROOT, is_png, load, store
from .logovariants import DEFAULT_VARIANT, FLAT_VARIANTS, VARIANTS, curated, is_variant

CDN = "https://a.espncdn.com/i/teamlogos/{sport}/500/{code}.png"
CDN_DARK = "https://a.espncdn.com/i/teamlogos/{sport}/500-dark/{code}.png"
TEAMS_API = "https://site.api.espn.com/apis/site/v2/sports/{path}/teams?limit=50"

LEAGUE_PATHS = {"nhl": "hockey/nhl", "nfl": "football/nfl"}
LOGO_DIR = CACHE_ROOT / "logos"
CONCURRENCY = 4
FETCH_TIMEOUT = 20.0
STORE_EDGE = 500        # ESPN's GUID art is 4096px; decoding that on a Pi costs ~67MB
WATCH_INTERVAL = 10.0
# ESPN's path segment is the lowercased abbreviation, bar a handful of legacy short codes
ESPN_CODES: dict[str, dict[str, str]] = {"nhl": {"LAK": "la", "SJS": "sj", "TBL": "tb"}}
# ...and its *team API* disagrees with the CDN for two more, so variant lookups need their own map
API_ABBREVS: dict[str, dict[str, str]] = {"nhl": {"LAK": "LA", "SJS": "SJ", "TBL": "TB", "NJD": "NJ", "UTA": "UTAH"}}

_preferences: dict[str, str] = {}       # "nhl:WSH" -> variant
_use_curated = True
_generation = 0                         # bumped on every config change; watchers top up on change


def espn_code(sport: str, abbrev: str) -> str:
    return ESPN_CODES.get(sport, {}).get(abbrev.upper(), abbrev.lower())


def api_abbrev(sport: str, abbrev: str) -> str:
    """The abbreviation ESPN's team API uses, which is not always the one on its CDN."""
    return API_ABBREVS.get(sport, {}).get(abbrev.upper(), abbrev.upper())


def path(sport: str, abbrev: str, variant: str = DEFAULT_VARIANT) -> Path:
    """Cache path. The default variant keeps the bare filename, so old caches still count."""
    name = abbrev.upper() if variant == DEFAULT_VARIANT else f"{abbrev.upper()}__{variant}"
    return LOGO_DIR / sport / f"{name}.png"


# -- preferences ------------------------------------------------------------


def apply_config(use_curated: bool, overrides: Mapping[str, str]) -> None:
    """Install the configured logo choices. Safe to call from any thread."""
    global _preferences, _use_curated, _generation
    _preferences = {k.strip(): v for k, v in overrides.items() if is_variant(v)}
    _use_curated = use_curated
    _generation += 1


def generation() -> int:
    return _generation


def preference(sport: str, abbrev: str) -> str:
    """The variant this team should use: an explicit override, else the audited default."""
    override = _preferences.get(f"{sport}:{abbrev.upper()}")
    if override:
        return override
    return (curated(sport, abbrev) if _use_curated else None) or DEFAULT_VARIANT


# -- reading ----------------------------------------------------------------


def logo(sport: str, abbrev: str, size: int, variant: str | None = None) -> Image.Image | None:
    """The cached logo scaled to fit a ``size`` square, or None if it hasn't been fetched.

    Falls back to the default variant when the preferred one has not landed yet, so a
    freshly-changed preference degrades to the old art rather than to a placeholder.
    """
    chosen = variant or preference(sport, abbrev)
    if chosen != DEFAULT_VARIANT:
        img = load(path(sport, abbrev, chosen), size)
        if img is not None:
            return img
    return load(path(sport, abbrev), size)


# -- fetching ---------------------------------------------------------------


async def watch(http: httpx.AsyncClient, sport: str, abbrevs: tuple[str, ...], log,
                interval: float = WATCH_INTERVAL) -> None:
    """Fetch the league's logos, then keep the cache in step with preference changes.

    Sources gather this for the life of the process. It is idle bar a generation compare
    once everything wanted is on disk.
    """
    seen = -1
    while True:
        current = _generation
        if current != seen:
            await prefetch(http, sport, abbrevs, log)
            seen = current
        await asyncio.sleep(interval)


async def prefetch(http: httpx.AsyncClient, sport: str, abbrevs: tuple[str, ...], log) -> int:
    """Download whatever this league is missing, default art and preferred variants alike.

    Safe to call repeatedly: it's a no-op once everything wanted is cached.
    """
    wanted = {(a, DEFAULT_VARIANT) for a in abbrevs} | {(a, preference(sport, a)) for a in abbrevs}
    missing = sorted({(a, v) for a, v in wanted if not path(sport, a, v).is_file()})
    if not missing:
        return 0

    index: dict[str, dict[str, str]] = {}
    if any(v not in FLAT_VARIANTS for _, v in missing):
        index = await _discover(http, sport, log)

    log.info("fetching %d %s team logos (one time, then cached in %s)", len(missing), sport, LOGO_DIR / sport)
    limit = asyncio.Semaphore(CONCURRENCY)

    async def one(abbrev: str, variant: str) -> bool:
        async with limit:
            return await _fetch(http, sport, abbrev, variant, index, log)

    got = sum(await asyncio.gather(*(one(a, v) for a, v in missing)))
    log.info("cached %d/%d %s logos", got, len(missing), sport)
    return got


async def _discover(http: httpx.AsyncClient, sport: str, log) -> dict[str, dict[str, str]]:
    """Map each team abbreviation to its available variant URLs, via ESPN's team API.

    The branded variants live on a per-team GUID path that is only discoverable here.
    """
    league = LEAGUE_PATHS.get(sport)
    if league is None:
        return {}
    try:
        resp = await http.get(TEAMS_API.format(path=league), timeout=FETCH_TIMEOUT, follow_redirects=True,
                              headers=ESPN_HEADERS)
        resp.raise_for_status()
        leagues = resp.json()["sports"][0]["leagues"][0]["teams"]
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        log.warning("could not list %s teams for logo variants: %s", sport, exc)
        return {}
    index: dict[str, dict[str, str]] = {}
    for entry in leagues:
        team = entry.get("team") or {}
        abbrev = str(team.get("abbreviation") or "").upper()
        if not abbrev:
            continue
        index[abbrev] = {"/".join(item.get("rel", [])): item.get("href", "") for item in team.get("logos") or []}
    return index


def _url(sport: str, abbrev: str, variant: str, index: Mapping[str, Mapping[str, str]]) -> str | None:
    if variant == DEFAULT_VARIANT:
        return CDN.format(sport=sport, code=espn_code(sport, abbrev))
    if variant == "dark":
        return CDN_DARK.format(sport=sport, code=espn_code(sport, abbrev))
    rel = VARIANTS.get(variant)
    return (index.get(api_abbrev(sport, abbrev)) or {}).get(rel or "") or None


async def _fetch(http: httpx.AsyncClient, sport: str, abbrev: str, variant: str,
                 index: Mapping[str, Mapping[str, str]], log) -> bool:
    url = _url(sport, abbrev, variant, index)
    if not url:
        log.warning("%s has no %s logo for %s", sport, variant, abbrev)
        return False
    try:
        resp = await http.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("logo fetch failed for %s %s (%s): %s", sport, abbrev, variant, exc)
        return False
    if not is_png(resp.content):
        log.warning("logo for %s %s was not a usable PNG (%d bytes)", sport, abbrev, len(resp.content))
        return False
    return store(path(sport, abbrev, variant), _downscale(resp.content, log), log)


def _downscale(content: bytes, log) -> bytes:
    """Shrink oversized art before it hits the cache, so the Pi never decodes 4096px."""
    from io import BytesIO

    try:
        with Image.open(BytesIO(content)) as src:
            if max(src.size) <= STORE_EDGE:
                return content
            img = src.convert("RGBA")
        img.thumbnail((STORE_EDGE, STORE_EDGE), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "PNG", optimize=True)
    except (OSError, ValueError) as exc:
        log.warning("could not downscale logo, caching as-is: %s", exc)
        return content
    return buf.getvalue()
