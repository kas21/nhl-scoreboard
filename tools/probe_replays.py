"""Watch one NHL game and record when each goal's tracking replay becomes available.

The question: does the league write a goal's puck-and-player tracking file
(``wsr.nhle.com/sprites/{season}/{game}/ev{event}.json``, the ``pptReplayUrl`` on the landing
feed's goals) during the game, or only in a batch after the final horn? Two finished games
showed one last-modified time for every goal, minutes after the end — but a last-modified
time only says when the file was last written.

Every ``--interval`` seconds this polls the score feed (state, clock, score), the landing feed
(which goals it lists, and which carry a ``pptReplayUrl``) and, for every goal the landing has
listed, the sprite file at its constructed URL — before the landing publishes that URL, so a file
that exists ahead of the URL shows up too. It logs a heartbeat per poll and a line for every
change, keeps on for ``--linger`` minutes after the final, and prints a per-goal table on exit
(Ctrl-C included): when the goal was first listed, when its URL appeared, when the file first
answered 200, and the file's last-modified header.

    uv run python tools/probe_replays.py                      # tonight's first game not yet over
    uv run python tools/probe_replays.py --team TOR           # a favourite's game today
    uv run python tools/probe_replays.py --game 2026020006 --once   # one look at a finished game
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

API = "https://api-web.nhle.com/v1"
SPRITES = "https://wsr.nhle.com/sprites"
BROWSER = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Origin": "https://www.nhl.com",
    "Referer": "https://www.nhl.com/",
    "Accept": "application/json, text/plain, */*",
}
FINAL_STATES = {"OFF", "FINAL"}


def now() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S")


class Probe:
    def __init__(self, game_id: int, log_path: Path, interval: float, linger_minutes: float) -> None:
        self.game_id = game_id
        self.log_path = log_path
        self.interval = interval
        self.linger = linger_minutes * 60
        self.goals: dict[int, dict[str, Any]] = {}      # event id -> what we know and when we learned it
        self.season: int | None = None
        self.final_at: float | None = None
        self.last_heartbeat = ""

    # -- output --------------------------------------------------------------------

    def log(self, line: str) -> None:
        text = f"{now()}  {line}"
        print(text, flush=True)
        with self.log_path.open("a") as fh:
            fh.write(text + "\n")

    def summary(self) -> str:
        rows = ["", f"game {self.game_id}: goal -> listed in landing -> URL published -> file answers 200 (UTC)",
                f"{'event':>6} {'period':>6} {'time':>6} {'team':>4}  {'listed':>8}  {'url':>8}  {'file 200':>8}  {'lag':>6}  last-modified"]
        for ev, g in sorted(self.goals.items(), key=lambda kv: kv[1].get("order", 0)):
            lag = ""
            if g.get("file_at") and g.get("listed_at"):
                lag = f"{(g['file_at'] - g['listed_at']) / 60:5.1f}m"
            rows.append(f"{ev:>6} {g.get('period', ''):>6} {g.get('time', ''):>6} {g.get('team', ''):>4}  "
                        f"{g.get('listed', ''):>8}  {g.get('url', ''):>8}  {g.get('file', ''):>8}  {lag:>6}  {g.get('last_modified', '')}")
        if not self.goals:
            rows.append("  no goals seen")
        return "\n".join(rows)

    # -- polling ---------------------------------------------------------------------

    async def tick(self, http: httpx.AsyncClient) -> bool:
        """One look at everything. Returns False when it is time to stop."""
        state, clock = await self._score(http)
        landing = await self._landing(http)
        if landing:
            self.season = self.season or landing.get("season")
            listed = self._goals_in(landing)
            for order, g in enumerate(listed):
                ev = g.get("eventId")
                if ev is None:
                    continue
                known = self.goals.setdefault(ev, {"order": order})
                if "listed" not in known:
                    known.update({"listed": now(), "listed_at": time.time(), "period": g.get("_period"), "time": g.get("timeInPeriod", ""),
                                  "team": (g.get("teamAbbrev") or {}).get("default", "")})
                    self.log(f"goal {ev}: listed in landing ({known['team']} P{known['period']} {known['time']}); url {'present' if g.get('pptReplayUrl') else 'absent'}")
                if g.get("pptReplayUrl") and "url" not in known:
                    known["url"] = now()
                    self.log(f"goal {ev}: pptReplayUrl published: {g['pptReplayUrl']}")
        if self.season:
            for ev, known in self.goals.items():
                if "file" in known:
                    continue
                status, last_modified = await self._sprite(http, ev)
                if status != known.get("last_status"):
                    known["last_status"] = status
                    self.log(f"goal {ev}: file answers {status}")
                if status == 200:
                    known.update({"file": now(), "file_at": time.time(), "last_modified": last_modified})
                    self.log(f"goal {ev}: file is there (last-modified {last_modified})")
        heartbeat = f"{state} {clock}  goals listed {len(self.goals)}  files {sum('file' in g for g in self.goals.values())}"
        if heartbeat != self.last_heartbeat:
            self.log(f"-- {heartbeat}")
            self.last_heartbeat = heartbeat
        if state in FINAL_STATES:
            if self.final_at is None:
                self.final_at = time.time()
                self.log(f"game is {state}; lingering {self.linger / 60:.0f} min for the batch")
            elif time.time() - self.final_at > self.linger:
                return False
        return True

    async def _score(self, http: httpx.AsyncClient) -> tuple[str, str]:
        try:
            resp = await http.get(f"{API}/score/now")
            resp.raise_for_status()
            game = next((g for g in resp.json().get("games") or [] if g.get("id") == self.game_id), None)
        except (httpx.HTTPError, ValueError) as exc:
            self.log(f"score fetch failed: {exc}")
            return "?", ""
        if game is None:
            return "not on today's slate", ""
        pd = game.get("periodDescriptor") or {}
        clock = (game.get("clock") or {}).get("timeRemaining", "")
        score = f"{(game.get('awayTeam') or {}).get('abbrev')} {(game.get('awayTeam') or {}).get('score', 0)}-{(game.get('homeTeam') or {}).get('score', 0)} {(game.get('homeTeam') or {}).get('abbrev')}"
        return game.get("gameState", "?"), f"P{pd.get('number', '')} {clock} {score}"

    async def _landing(self, http: httpx.AsyncClient) -> dict[str, Any] | None:
        try:
            resp = await http.get(f"{API}/gamecenter/{self.game_id}/landing")
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            self.log(f"landing fetch failed: {exc}")
            return None

    @staticmethod
    def _goals_in(landing: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for period in ((landing.get("summary") or {}).get("scoring") or []):
            number = (period.get("periodDescriptor") or {}).get("number")
            out.extend({**g, "_period": number} for g in period.get("goals") or [])
        return out

    async def _sprite(self, http: httpx.AsyncClient, ev: int) -> tuple[int | str, str]:
        url = f"{SPRITES}/{self.season}/{self.game_id}/ev{ev}.json"
        try:
            resp = await http.get(url, headers=BROWSER)
        except httpx.HTTPError as exc:
            return f"error {type(exc).__name__}", ""
        return resp.status_code, resp.headers.get("last-modified", "")


async def pick_game(http: httpx.AsyncClient, team: str | None) -> int:
    resp = await http.get(f"{API}/score/now")
    resp.raise_for_status()
    games = resp.json().get("games") or []
    if team:
        for g in games:
            if team.upper() in ((g.get("awayTeam") or {}).get("abbrev"), (g.get("homeTeam") or {}).get("abbrev")):
                return g["id"]
        sys.exit(f"{team} is not on today's slate")
    upcoming = [g for g in games if g.get("gameState") not in FINAL_STATES] or games
    if not upcoming:
        sys.exit("no games today")
    g = sorted(upcoming, key=lambda g: g.get("startTimeUTC", ""))[0]
    print(f"watching {g['awayTeam']['abbrev']} @ {g['homeTeam']['abbrev']} ({g['id']}), starts {g.get('startTimeUTC')}")
    return g["id"]


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--game", type=int, help="game id (default: today's first game that is not over)")
    ap.add_argument("--team", help="watch this team's game today instead")
    ap.add_argument("--interval", type=float, default=60, help="seconds between looks (default 60)")
    ap.add_argument("--linger", type=float, default=45, help="minutes to keep looking after the final (default 45)")
    ap.add_argument("--log", type=Path, help="log file (default replay_probe_<game>.log in the current directory)")
    ap.add_argument("--once", action="store_true", help="one look, then the summary")
    args = ap.parse_args()
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "nhl-scoreboard-probe"}) as http:
        game_id = args.game or await pick_game(http, args.team)
        probe = Probe(game_id, args.log or Path(f"replay_probe_{game_id}.log"), args.interval, args.linger)
        probe.log(f"probing game {game_id} every {args.interval:.0f}s; log {probe.log_path}")
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        try:
            while not stop.is_set():
                if not await probe.tick(http) or args.once:
                    break
                try:
                    await asyncio.wait_for(stop.wait(), timeout=args.interval)
                except TimeoutError:
                    pass
        finally:
            probe.log(probe.summary())
            (probe.log_path.with_suffix(".json")).write_text(json.dumps(probe.goals, indent=1, default=str))


if __name__ == "__main__":
    asyncio.run(main())
