"""Thin async client for the AHL's HockeyTech (LeagueStat) feed.

``lscluster.hockeytech.com`` is what theahl.com itself reads; the key below is the public one
embedded in that site, and ``client_code=ahl`` picks the league. Two feed families are used:
``modulekit`` views (score bar, seasons, teams, a club schedule), which answer
``{"SiteKit": {...}}``, and the ``gc`` game-centre tabs (summary with goals, penalties and
shots), which answer ``{"GC": {...}}``. Standings come from the ``statviewfeed`` family, whose
JSON arrives wrapped in parentheses (it is meant for a JSONP callback); ``_get`` unwraps it.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://lscluster.hockeytech.com/feed/index.php"
KEY = "ccb91f29d6744675"
CLIENT = "ahl"
LEAGUE_ID = 4
RETRY_DELAYS = (1.0, 3.0, 8.0)


class AhlApiError(Exception):
    pass


class AhlApi:
    def __init__(self, http: httpx.AsyncClient, base_url: str = BASE_URL) -> None:
        self._http = http
        self._base = base_url

    async def scorebar(self, days_back: int = 1, days_ahead: int = 2) -> dict[str, Any]:
        """Every game from ``days_back`` days ago to ``days_ahead`` days out, with live period, clock and score."""
        return await self._sitekit(view="scorebar", numberofdaysback=days_back, numberofdaysahead=days_ahead)

    async def seasons(self) -> dict[str, Any]:
        return await self._sitekit(view="seasons")

    async def teams(self, season_id: int | None = None) -> dict[str, Any]:
        return await self._sitekit(view="teamsbyseason", **({"season_id": season_id} if season_id else {}))

    async def schedule(self, season_id: int, team_id: int | str) -> dict[str, Any]:
        return await self._sitekit(view="schedule", season_id=season_id, team_id=team_id)

    async def game_summary(self, game_id: int | str) -> dict[str, Any]:
        data = await self._get(feed="gc", tab="gamesummary", game_id=game_id)
        return (data.get("GC") or {}).get("Gamesummary") or {}

    async def standings(self, season_id: int) -> list[dict[str, Any]]:
        data = await self._get(feed="statviewfeed", view="teams", groupTeamsBy="division", context="overall", site_id=0,
                               season=season_id, special="false", league_id=LEAGUE_ID, division=-1, sort="points",
                               conference=-1, lang="en")
        return data if isinstance(data, list) else []

    async def _sitekit(self, **params: Any) -> dict[str, Any]:
        data = await self._get(feed="modulekit", **params)
        return data.get("SiteKit") or {}

    async def _get(self, **params: Any) -> Any:
        query = {"key": KEY, "client_code": CLIENT, "fmt": "json", **params}
        last: Exception | None = None
        for delay in (*RETRY_DELAYS, None):
            try:
                resp = await self._http.get(self._base, params=query, follow_redirects=True)
                if resp.status_code == 429:
                    await asyncio.sleep(float(resp.headers.get("Retry-After", delay or 30)))
                    continue
                resp.raise_for_status()
                return _decode(resp.text)
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                if delay is None:
                    break
                await asyncio.sleep(delay)
        raise AhlApiError(f"GET {params.get('feed')}/{params.get('view') or params.get('tab')} failed: {last}") from last


def _decode(text: str) -> Any:
    """The statviewfeed wraps its JSON as ``(...)``; everything else is plain."""
    import json

    body = text.strip()
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1]
    data = json.loads(body)
    if not isinstance(data, dict | list):
        raise ValueError("unexpected payload")
    return data
