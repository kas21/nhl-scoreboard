"""Follower source: take every snapshot key from another scoreboard instead of fetching.

One panel on the network does the polling (the *master*); a follower asks it for whatever
changed since the last snapshot version it saw, over the master's own ``/api/snapshot``,
and republishes each key into its own store. From there nothing is different: the
detectors fire, the director picks boards, the panel renders — with this box's display,
brightness, playlists and board settings.

The master answers a ``since`` request only once something has changed (or after ``wait``
seconds), so a goal reaches the follower within a round trip rather than a poll interval.
A master restart resets its version counter; the follower notices the number going
backwards and starts over from a full snapshot. Losing the master marks the panel
offline after a few failures, like a sport source that cannot reach its league.

Logos are the one thing not relayed: the follower fetches them from the CDN itself, for
the teams it sees in the relayed scores and standings, so a follower needs the internet
for artwork but nothing else.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, ConfigDict

from . import logos
from .config.models import FollowerConfig
from .data.source import SourceContext

log = logging.getLogger(__name__)

WAIT_SECONDS = 25                       # long-poll: how long the master may hold a request open
MIN_ROUND_SECONDS = 0.25                # never ask faster than this: a master too old to hold a request answers at once
OFFLINE_AFTER_FAILURES = 3              # consecutive failed rounds before the panel says offline
RETRY_DELAYS = (2, 5, 10, 30)
IDLE_RECHECK_SECONDS = 5                # how often a disabled follower looks at its config again
LOGO_SPORTS = ("nhl", "nfl", "ncaaf", "mlb")


class _NoSettings(BaseModel):
    """The follower is configured from the top-level *Follower* section, not per source."""

    model_config = ConfigDict(frozen=True, extra="ignore", title="Follower")


class FollowerSource:
    key: ClassVar[str] = "follower"
    config_model: ClassVar[type[BaseModel]] = _NoSettings

    def __init__(self, config_getter: Callable[[], FollowerConfig]) -> None:
        self._config = config_getter
        self._teams: dict[str, set[str]] = {}          # sport -> abbrevs whose logos we want
        self._logo_tasks: dict[str, asyncio.Task[Any]] = {}
        self._logo_generation = -1

    async def run(self, ctx: SourceContext) -> None:
        since = -1                                      # -1: everything, please
        failures = 0
        try:
            while True:
                cfg = self._config()
                url = master_url(cfg)
                if not cfg.enabled or url is None:
                    await ctx.sleep(IDLE_RECHECK_SECONDS)
                    continue
                started = time.monotonic()
                try:
                    body = await self._fetch(ctx, url, since)
                except (httpx.HTTPError, ValueError) as exc:
                    failures += 1
                    ctx.log.warning("master %s unreachable (%s in a row): %s", url, failures, exc)
                    if failures >= OFFLINE_AFTER_FAILURES:
                        ctx.publish_to("system", {"online": False, "failures": failures, "master": url})
                    since = -1                          # whatever we missed, take it all on reconnect
                    await ctx.sleep(RETRY_DELAYS[min(failures - 1, len(RETRY_DELAYS) - 1)])
                    continue
                failures = 0
                version = body["version"]
                if version < since:                     # the master restarted: its counter is young again
                    ctx.log.info("master restarted; resyncing from a full snapshot")
                    since = -1
                    continue
                for key, value in body["data"].items():
                    ctx.publish_to(key, value)
                since = version
                self._want_logos(ctx, body["data"])
                await asyncio.sleep(max(MIN_ROUND_SECONDS - (time.monotonic() - started), 0))
        finally:
            for task in self._logo_tasks.values():
                task.cancel()

    async def _fetch(self, ctx: SourceContext, url: str, since: int) -> dict[str, Any]:
        resp = await ctx.http.get(url, params={"since": since, "wait": WAIT_SECONDS}, timeout=WAIT_SECONDS + 10)
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict) or not isinstance(body.get("version"), int) or not isinstance(body.get("data"), dict):
            raise ValueError("not a scoreboard snapshot")
        return body

    # -- logos -----------------------------------------------------------------

    def _want_logos(self, ctx: SourceContext, data: dict[str, Any]) -> None:
        """Fetch artwork for any team the relayed data mentions that we have not seen yet."""
        generation = logos.generation()
        refresh_all = generation != self._logo_generation
        self._logo_generation = generation
        for sport in LOGO_SPORTS:
            found = teams_in(sport, data)
            new = found - self._teams.setdefault(sport, set())
            self._teams[sport] |= found
            if not self._teams[sport] or (not new and not refresh_all):
                continue
            running = self._logo_tasks.get(sport)
            if running is not None and not running.done():
                continue                                # this batch's teams are already in the set it works from
            self._logo_tasks[sport] = asyncio.create_task(
                logos.prefetch(ctx.http, sport, tuple(sorted(self._teams[sport])), ctx.log), name=f"logos:{sport}")


def master_url(cfg: FollowerConfig) -> str | None:
    """The master's snapshot endpoint, or None when the address is blank or not a URL."""
    base = cfg.master_url.strip().rstrip("/")
    if not base:
        return None
    if not base.startswith(("http://", "https://")):
        base = "http://" + base
    return base + "/api/snapshot"


def teams_in(sport: str, data: dict[str, Any]) -> set[str]:
    """Team abbreviations a sport's relayed keys mention (scores, standings, team summaries)."""
    found: set[str] = set()
    for game in data.get(f"{sport}.scores") or []:
        for side in ("away", "home"):
            abbrev = (game.get(side) or {}).get("abbrev") if isinstance(game, dict) else None
            if abbrev:
                found.add(abbrev)
    standings = data.get(f"{sport}.standings")
    if isinstance(standings, dict) and isinstance(standings.get("teams"), dict):
        found.update(standings["teams"])
    summary = data.get(f"{sport}.team_summary")
    if isinstance(summary, dict):
        found.update(summary)
    return {a for a in found if isinstance(a, str)}
