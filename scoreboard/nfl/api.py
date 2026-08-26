"""ESPN public site API for the NFL (no key)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)
SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
STANDINGS = "https://site.api.espn.com/apis/v2/sports/football/nfl/standings"
RETRY_DELAYS = (1.0, 3.0, 8.0)


class NflApiError(Exception):
    pass


class NflApi:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def scoreboard(self, dates: str | None = None) -> dict[str, Any]:
        return await self._get(f"{SITE}/scoreboard", params={"dates": dates} if dates else None)

    async def standings(self) -> dict[str, Any]:
        return await self._get(STANDINGS)

    async def teams(self) -> dict[str, Any]:
        return await self._get(f"{SITE}/teams")

    async def team_schedule(self, team_id: str) -> dict[str, Any]:
        return await self._get(f"{SITE}/teams/{team_id}/schedule")

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last: Exception | None = None
        for delay in (*RETRY_DELAYS, None):
            try:
                resp = await self._http.get(url, params=params, follow_redirects=True)
                if resp.status_code == 429:
                    await asyncio.sleep(float(resp.headers.get("Retry-After", delay or 30)))
                    continue
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, dict):
                    raise NflApiError("unexpected payload")
                return data
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                if delay is None:
                    break
                await asyncio.sleep(delay)
        raise NflApiError(f"GET {url} failed: {last}") from last
