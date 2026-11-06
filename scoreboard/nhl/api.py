"""Thin async client for api-web.nhle.com with retry and 429 back-off."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://api-web.nhle.com/v1"
RETRY_DELAYS = (1.0, 3.0, 8.0)
LEADERS_SEASON = re.compile(r"/(\d{8})/(\d)$")     # .../skater-stats-leaders/20262027/2 — where "current" redirects to


class NhlApiError(Exception):
    pass


class NhlApi:
    def __init__(self, http: httpx.AsyncClient, base_url: str = BASE_URL) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def score(self, date: str = "now") -> dict[str, Any]:
        return await self._get(f"/score/{date}")

    async def standings(self, date: str = "now") -> dict[str, Any]:
        return await self._get(f"/standings/{date}")

    async def landing(self, game_id: int | str) -> dict[str, Any]:
        return await self._get(f"/gamecenter/{game_id}/landing")

    async def right_rail(self, game_id: int | str) -> dict[str, Any]:
        """Game Center's sidebar: team game stats, shots by period, linescore."""
        return await self._get(f"/gamecenter/{game_id}/right-rail")

    async def schedule_now(self) -> dict[str, Any]:
        return await self._get("/schedule/now")

    async def schedule(self, start: str) -> dict[str, Any]:
        """The game week starting on ``start`` (YYYY-MM-DD); ``nextStartDate`` links to the following one."""
        return await self._get(f"/schedule/{start}")

    async def club_schedule_season(self, team: str) -> dict[str, Any]:
        return await self._get(f"/club-schedule-season/{team.upper()}/now")

    async def skater_leaders(self, categories: tuple[str, ...], limit: int) -> dict[str, Any]:
        """The league's top skaters per category: ``{category: [player...]}``, best first."""
        return await self._leaders("skater-stats-leaders", categories, limit)

    async def goalie_leaders(self, categories: tuple[str, ...], limit: int = -1) -> dict[str, Any]:
        """The same for goalies. ``limit=-1`` is every goalie the league ranks in the category
        (save percentage and goals-against only list goalies with enough games to qualify)."""
        return await self._leaders("goalie-stats-leaders", categories, limit)

    async def _leaders(self, kind: str, categories: tuple[str, ...], limit: int) -> dict[str, Any]:
        """``current`` redirects to ``/{season}/{gameType}`` and the payload itself says neither,
        so both are read off the final URL and added as ``season`` / ``gameType`` (the names
        club-stats uses): the playoffs' leaders are a different list from the season's."""
        final: list[str] = []
        data = await self._get(f"/{kind}/current?categories={','.join(categories)}&limit={limit}", final)
        found = LEADERS_SEASON.search(final[0]) if final else None
        if found:
            data.setdefault("season", found.group(1))
            data.setdefault("gameType", int(found.group(2)))
        return data

    async def club_stats(self, team: str) -> dict[str, Any]:
        """Every skater's and goalie's season line for one club (``skaters``, ``goalies``)."""
        return await self._get(f"/club-stats/{team.upper()}/now")

    async def _get(self, path: str, final_path: list[str] | None = None) -> dict[str, Any]:
        """``final_path``, when given, receives the path the request ended on after redirects."""
        url = f"{self._base}{path}"
        last: Exception | None = None
        for attempt, delay in enumerate((*RETRY_DELAYS, None)):
            try:
                resp = await self._http.get(url, follow_redirects=True)
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", delay or 30))
                    log.warning("NHL API rate limited on %s; waiting %ss", path, wait)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, dict):
                    raise NhlApiError(f"unexpected payload for {path}")
                if final_path is not None:
                    final_path.append(resp.url.path)
                return data
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                if delay is None:
                    break
                log.debug("NHL API %s failed (attempt %s): %s", path, attempt + 1, exc)
                await asyncio.sleep(delay)
        raise NhlApiError(f"GET {path} failed: {last}") from last
