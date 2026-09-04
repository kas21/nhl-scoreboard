"""MLB Stats API (statsapi.mlb.com) — public, no key. The same feed MLB-LED-Scoreboard uses."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)
BASE_URL = "https://statsapi.mlb.com/api/v1"
FEED_URL = "https://statsapi.mlb.com/api/v1.1"
SPORT_ID = 1                    # MLB
LEAGUE_IDS = "103,104"          # AL, NL
RETRY_DELAYS = (1.0, 3.0, 8.0)
SCHEDULE_HYDRATE = "team,linescore,probablePitcher,decisions"
# The live feed is ~1MB unfiltered; ask only for what the boards and detectors read.
FEED_FIELDS = (
    "gamePk,gameData,status,detailedState,abstractGameState,flags,noHitter,perfectGame,"
    "liveData,plays,currentPlay,result,eventType,event,description,rbi,awayScore,homeScore,"
    "about,isComplete,isTopInning,halfInning,inning,isScoringPlay,"
    "playEvents,isPitch,pitchData,startSpeed,details,type,code,description,call,"
    "linescore,defense,pitcher,id,fullName,"
    "decisions,winner,loser,save,"
    "boxscore,teams,home,away,players,stats,pitching,numberOfPitches,seasonStats,wins,losses,saves,era"
)


class MlbApiError(Exception):
    pass


class MlbApi:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def schedule(self, start_date: str, end_date: str | None = None, team_id: int | None = None,
                       hydrate: str = SCHEDULE_HYDRATE) -> dict[str, Any]:
        params: dict[str, Any] = {"sportId": SPORT_ID, "startDate": start_date, "endDate": end_date or start_date,
                                  "hydrate": hydrate}
        if team_id:
            params["teamId"] = team_id
        return await self._get(f"{BASE_URL}/schedule", params)

    async def live_feed(self, game_pk: int | str) -> dict[str, Any]:
        return await self._get(f"{FEED_URL}/game/{game_pk}/feed/live", {"fields": FEED_FIELDS})

    async def standings(self, season: int) -> dict[str, Any]:
        return await self._get(f"{BASE_URL}/standings", {"leagueId": LEAGUE_IDS, "season": season,
                                                          "standingsTypes": "regularSeason"})

    async def season(self, season: int) -> dict[str, Any]:
        return await self._get(f"{BASE_URL}/seasons", {"sportId": SPORT_ID, "season": season})

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
                    raise MlbApiError("unexpected payload")
                return data
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                if delay is None:
                    break
                await asyncio.sleep(delay)
        raise MlbApiError(f"GET {url} failed: {last}") from last
