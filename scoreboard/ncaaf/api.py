"""ESPN public site API for college football (no key): the NFL client with the FBS URLs.

Without the ``groups=80`` (FBS) filter ESPN's college scoreboard only lists games that
involve a ranked team, and the default page size hides most of a Saturday slate.
"""
from __future__ import annotations

from typing import Any, ClassVar

from ..nfl.api import NflApi, NflApiError

FBS_GROUP = 80
SITE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
STANDINGS = "https://site.api.espn.com/apis/v2/sports/football/college-football/standings"
NcaafApiError = NflApiError


class NcaafApi(NflApi):
    site = SITE
    standings_url = STANDINGS
    scoreboard_params: ClassVar[dict[str, Any]] = {"groups": FBS_GROUP, "limit": 200}
    standings_params: ClassVar[dict[str, Any]] = {"group": FBS_GROUP}
    teams_params: ClassVar[dict[str, Any]] = {"groups": FBS_GROUP, "limit": 200}
