"""ESPN public site API for the NFL (no key). Subclass and swap the URLs for another football league."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

import httpx

from ..espn import HEADERS as ESPN_HEADERS

log = logging.getLogger(__name__)
SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
STANDINGS = "https://site.api.espn.com/apis/v2/sports/football/nfl/standings"
RETRY_DELAYS = (1.0, 3.0, 8.0)


class NflApiError(Exception):
    pass


class NflApi:
    site: str = SITE
    standings_url: str = STANDINGS
    scoreboard_params: ClassVar[dict[str, Any]] = {}          # e.g. the FBS group filter for college
    standings_params: ClassVar[dict[str, Any]] = {}
    teams_params: ClassVar[dict[str, Any]] = {}

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def scoreboard(self, dates: str | None = None) -> dict[str, Any]:
        params = {**self.scoreboard_params, **({"dates": dates} if dates else {})}
        return await self._get(f"{self.site}/scoreboard", params=params or None)

    async def standings(self) -> dict[str, Any]:
        return await self._get(self.standings_url, params=self.standings_params or None)

    async def teams(self) -> dict[str, Any]:
        return await self._get(f"{self.site}/teams", params=self.teams_params or None)

    async def team_schedule(self, team_id: str) -> dict[str, Any]:
        return await self._get(f"{self.site}/teams/{team_id}/schedule")

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last: Exception | None = None
        for delay in (*RETRY_DELAYS, None):
            try:
                resp = await self._http.get(url, params=params, follow_redirects=True, headers=ESPN_HEADERS)
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
