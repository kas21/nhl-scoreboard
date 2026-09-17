"""ESPN public site API for men's college hockey (no key): the NFL client with the hockey URLs.

The scoreboard lists the whole Division I slate by default (there is no "ranked only" filter
to undo, unlike football), so the only parameter is a page size that fits a busy Saturday.
The teams endpoint knows every programme ESPN has ever scored (~120, D3 and Canadian
schools included), so it is asked for all of them and the source keeps the D1 ones.

ESPN's *standings* endpoint for this league is a shell: conferences with no rows in them.
Nothing here reads it; records come from the favourites' schedules instead.
"""
from __future__ import annotations

from typing import Any, ClassVar

from ..nfl.api import NflApi, NflApiError

SITE = "https://site.api.espn.com/apis/site/v2/sports/hockey/mens-college-hockey"
NcaahApiError = NflApiError


class NcaahApi(NflApi):
    site = SITE
    standings_url = f"{SITE}/standings"
    scoreboard_params: ClassVar[dict[str, Any]] = {"limit": 200}
    teams_params: ClassVar[dict[str, Any]] = {"limit": 1000}

    async def team_schedule(self, team_id: str, season: int | None = None) -> dict[str, Any]:
        params = {"season": season} if season else None
        return await self._get(f"{self.site}/teams/{team_id}/schedule", params=params)
