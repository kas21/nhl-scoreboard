"""Thin async client for api-web.nhle.com with retry and 429 back-off."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://api-web.nhle.com/v1"
RETRY_DELAYS = (1.0, 3.0, 8.0)


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

    async def schedule_now(self) -> dict[str, Any]:
        return await self._get("/schedule/now")

    async def schedule(self, start: str) -> dict[str, Any]:
        """The game week starting on ``start`` (YYYY-MM-DD); ``nextStartDate`` links to the following one."""
        return await self._get(f"/schedule/{start}")

    async def club_schedule_season(self, team: str) -> dict[str, Any]:
        return await self._get(f"/club-schedule-season/{team.upper()}/now")

    async def _get(self, path: str) -> dict[str, Any]:
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
                return data
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                if delay is None:
                    break
                log.debug("NHL API %s failed (attempt %s): %s", path, attempt + 1, exc)
                await asyncio.sleep(delay)
        raise NhlApiError(f"GET {path} failed: {last}") from last
