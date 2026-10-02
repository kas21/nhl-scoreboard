"""Goal replays: the league's puck-and-player tracking clip behind each goal.

Game Center's goal animation is not a video. For every goal the landing feed carries a
``pptReplayUrl`` (PPT: puck and player tracking) pointing at a JSON file on
``wsr.nhle.com``: about fourteen seconds of positions at ten frames a second for the
twelve skaters and goalies on the ice and the puck, in inches from a corner of a 200 by
85 foot rink. The file is written three to four minutes after the goal, during the game
(tools/probe_replays.py measured it), and every goal's file is rewritten once more a few
minutes after the final horn — so the loop fetches during the game, then once more after it.

The bucket sits behind Cloudflare and answers a bare client with 403; a browser-shaped
request is let through. A goal without a file (the feed skips some) answers the same 403
as a goal whose file is not written yet — the two cannot be told apart, hence the bounded
retry rather than a verdict.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import httpx

from ..data import Event, Snapshot

log = logging.getLogger(__name__)

SPRITE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Origin": "https://www.nhl.com",
    "Referer": "https://www.nhl.com/",
    "Accept": "application/json, text/plain, */*",
}
RINK_LENGTH_IN = 2400       # 200 ft, the x axis of the tracking coordinates
RINK_WIDTH_IN = 1020        # 85 ft, the y axis
PUCK_ID = "1"               # the one entity in a frame with no player behind it
GOAL_LINE_IN = 132          # 11 ft from each end board
POST_HALF_IN = 36           # the goal mouth is 6 ft wide, centred on the rink
NET_SLACK_IN = 12           # a tracked puck against a post reads a little outside it
NET_MIN_FRAMES = 3          # a stretch in the net shorter than this is a tracking blip
FRAME_DECISECONDS = 1       # the feed's timestamps are tenths of a second, one per frame
MAIN_EVENT = "nhl.main_event"
REPLAYS = "nhl.goal_replays"
IN_PLAY = ("live", "intermission")


class SpriteUnavailable(Exception):
    """The file is not there (yet): a 403/404, not a network failure."""


async def fetch_sprite(http: httpx.AsyncClient, url: str) -> list[dict[str, Any]]:
    """The raw frame list behind a goal's ``pptReplayUrl``.

    Raises ``SpriteUnavailable`` when the bucket says no, ``httpx.HTTPError`` on a transport
    failure, ``ValueError`` when what came back is not a frame list."""
    resp = await http.get(url, headers=SPRITE_HEADERS, follow_redirects=True)
    if resp.status_code in (403, 404):
        raise SpriteUnavailable(f"{resp.status_code} for {url}")
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"replay at {url} is not a frame list")
    return data


def compact_replay(raw: list[dict[str, Any]], away_id: int | None, home_id: int | None) -> dict[str, Any] | None:
    """Shrink a raw sprite file to what a board draws.

    ``players`` lists everyone who appears in the clip (a line change mid-clip adds to it) with
    the side they play for; each frame carries the puck and ``[player index, x, y]`` for whoever
    is on the ice in it, as integer inches. Timestamps are kept as frame offsets from the first,
    so a gap in the feed is a gap on the panel too. ``goal_at`` is the offset of the frame the
    puck crossed the line (see ``goal_frame_offset``), None when the track never shows it.
    None when the file has nothing to draw."""
    if not raw:
        return None
    index: dict[str, int] = {}
    players: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    t0 = None
    for fr in raw:
        on_ice = fr.get("onIce") or {}
        if not isinstance(on_ice, dict):
            continue
        ts = fr.get("timeStamp")
        if t0 is None:
            t0 = ts
        puck = None
        placed: list[list[int]] = []
        for ent_id, ent in on_ice.items():
            if not isinstance(ent, dict):
                continue
            try:
                x, y = int(round(float(ent["x"]))), int(round(float(ent["y"])))
            except (KeyError, TypeError, ValueError):
                continue
            if str(ent_id) == PUCK_ID or not ent.get("playerId"):
                puck = [x, y]
                continue
            key = str(ent.get("playerId"))
            if key not in index:
                team_id = ent.get("teamId")
                side = "away" if team_id == away_id else "home" if team_id == home_id else "away" if away_id is None else "home"
                index[key] = len(players)
                players.append({"side": side, "sweater": _int(ent.get("sweaterNumber")), "player_id": _int(ent.get("playerId"))})
            placed.append([index[key], x, y])
        offset = 0 if t0 is None or not isinstance(ts, int | float) or not isinstance(t0, int | float) else int(ts - t0)
        frames.append({"t": offset, "puck": puck, "on_ice": placed})
    if not frames or not players:
        return None
    return {"fps": 10, "players": players, "frames": frames, "goal_at": goal_frame_offset(frames)}


def in_net(puck: list[int] | None) -> bool:
    """Whether a tracked puck is over a goal line between the posts (the net is open at the
    back, so anything from the line to the end boards counts)."""
    if not puck:
        return False
    x, y = puck
    return (x <= GOAL_LINE_IN or x >= RINK_LENGTH_IN - GOAL_LINE_IN) and abs(y - RINK_WIDTH_IN / 2) <= POST_HALF_IN + NET_SLACK_IN


def goal_frame_offset(frames: list[dict[str, Any]]) -> int | None:
    """When the goal went in: the file has no marker, but the puck's track does. After the goal
    the puck sits in the net until a referee fishes it out, so the goal is the first frame of
    the longest stretch the puck spent in a net; a stretch shorter than ``NET_MIN_FRAMES`` is a
    tracking blip, and a puck that went past the goal line outside the posts, or stopped in the
    crease, never counts. None when the track never shows the puck in a net."""
    best: tuple[int, int] | None = None       # (length, offset)
    start, length = None, 0
    for fr in frames + [{"puck": None}]:
        if in_net(fr.get("puck")):
            if start is None:
                start, length = fr["t"], 0
            length += 1
        elif start is not None:
            if length >= NET_MIN_FRAMES and (best is None or length > best[0]):
                best = (length, start)
            start = None
    return None if best is None else best[1]


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def landing_goals(landing: dict[str, Any]) -> list[dict[str, Any]]:
    """Every goal in the landing's scoring summary, flattened, with the period number on it."""
    out = []
    for period in ((landing.get("summary") or {}).get("scoring") or []):
        number = int((period.get("periodDescriptor") or {}).get("number") or 0)
        for g in period.get("goals") or []:
            out.append({**g, "_period": number})
    return out


def goal_entry(goal: dict[str, Any], clip: dict[str, Any] | None) -> dict[str, Any]:
    """One goal of the published ``nhl.goal_replays``: the scorer card plus the clip (or None)."""
    from .normalize import _goal, _text

    base = _goal({**goal, "periodDescriptor": {"number": goal.get("_period", 0)}})
    return {
        **base,
        "event_id": _int(goal.get("eventId")),
        "sweater": _int(goal.get("sweaterNumber")),
        "scorer_id": _int(goal.get("playerId")),
        "shot_type": _text(goal.get("shotType")) if isinstance(goal.get("shotType"), dict) else (goal.get("shotType") or ""),
        "home_defending_side": goal.get("homeTeamDefendingSide") or "",
        "clip": clip,
    }


def replay_candidate(snapshot: Any, favorites: list[str]) -> dict[str, Any] | None:
    """Which game to fetch replays for, with its phase: the favourite's game while it is on
    or once it is over, else the most recent result in its team summary (last night's game,
    the morning after). None with nothing to show, or until the scores loop has published
    at all (the team summary can land first; its last result may be a game the slate is
    about to say is on). The Simulator's game is never one: it has no landing and no files."""
    if not snapshot.has(MAIN_EVENT):
        return None
    main = snapshot.get(MAIN_EVENT) or {}
    if main.get("simulated"):
        return None
    if main.get("id") and (main.get("phase") in IN_PLAY
                           or (main.get("phase") == "postgame" and main.get("outcome", "").startswith("FINAL"))):
        return {"id": main["id"], "date": main.get("date", ""), "phase": main["phase"]}
    summaries = snapshot.get("nhl.team_summary") or {}
    for fav in favorites:
        prev = (summaries.get(fav) or {}).get("prev_game") or {}
        if prev.get("id") and prev.get("result"):
            return {"id": prev["id"], "date": prev.get("date", ""), "phase": "postgame"}
    return None


def keeps_recap(main: dict[str, Any] | None) -> bool:
    """Whether a main event that is not a candidate should leave the published recap up: the
    Simulator's game is no reason to forget a real result."""
    return bool((main or {}).get("simulated"))


def build_value(game_id: int, landing: dict[str, Any], clips: dict[int, dict[str, Any] | None],
                favorites: list[str]) -> dict[str, Any]:
    """The ``nhl.goal_replays`` value: the game, every goal from the landing's summary, and the
    clip for each goal that has one (None for the rest). ``favorite`` is the first favourite
    playing in it, so a board can keep to that team's goals."""
    away, home = landing.get("awayTeam") or {}, landing.get("homeTeam") or {}
    sides = {_abbrev(away), _abbrev(home)}
    return {
        "game_id": game_id,
        "date": landing.get("gameDate", ""),
        "state": landing.get("gameState", ""),
        "away": {"abbrev": _abbrev(away), "score": _int(away.get("score")) or 0},
        "home": {"abbrev": _abbrev(home), "score": _int(home.get("score")) or 0},
        "favorite": next((f for f in favorites if f in sides), ""),
        "goals": [goal_entry(g, clips.get(_int(g.get("eventId")))) for g in landing_goals(landing)],
    }


def replays_from_files(landing: dict[str, Any], sprites: dict[int, list[dict[str, Any]]],
                       favorites: list[str]) -> dict[str, Any]:
    """``build_value`` from raw files already on disk: the demo and the fixtures path."""
    away, home = landing.get("awayTeam") or {}, landing.get("homeTeam") or {}
    clips = {ev: compact_replay(raw, _int(away.get("id")), _int(home.get("id"))) for ev, raw in sprites.items()}
    return build_value(_int(landing.get("id")) or 0, landing, clips, favorites)


class ReplayFetcher:
    """Fetches the clips for one game and remembers which goals it still owes.

    ``refresh`` fetches the landing again (goals are added to it as they happen, and a goal's
    URL appears a few minutes later) and tries every goal that has a URL and no clip yet, a
    bounded number of times each. Returns the publishable value, or None when nothing changed.
    ``rewrite`` forgets every clip once, for the league's post-game rewrite of the files."""

    def __init__(self, game_id: int) -> None:
        self.game_id = game_id
        self.clips: dict[int, dict[str, Any] | None] = {}      # event id -> clip (None: a file with nothing in it)
        self.attempts: dict[int, int] = {}
        self.last_value: dict[str, Any] | None = None
        self.fetched_in_play = False        # a clip came in during the game: the post-game rewrite is worth a second look
        self.rewritten = False

    async def refresh(self, api: Any, http: httpx.AsyncClient, favorites: list[str], max_attempts: int,
                      in_play: bool = False) -> dict[str, Any] | None:
        landing = await api.landing(self.game_id)
        away, home = landing.get("awayTeam") or {}, landing.get("homeTeam") or {}
        for g in landing_goals(landing):
            ev = _int(g.get("eventId"))
            url = g.get("pptReplayUrl")
            if ev is None or not url or ev in self.clips or self.attempts.get(ev, 0) >= max_attempts:
                continue
            self.attempts[ev] = self.attempts.get(ev, 0) + 1
            try:
                raw = await fetch_sprite(http, url)
            except SpriteUnavailable as exc:
                log.debug("replay for goal %s not available yet: %s", ev, exc)
                continue
            except (httpx.HTTPError, ValueError) as exc:
                log.debug("replay fetch for goal %s failed: %s", ev, exc)
                continue
            self.clips[ev] = compact_replay(raw, _int(away.get("id")), _int(home.get("id")))
            self.fetched_in_play = self.fetched_in_play or in_play
        value = build_value(self.game_id, landing, self.clips, favorites)
        if value == self.last_value:
            return None
        self.last_value = value
        return value

    def rewrite(self) -> bool:
        """Once, after the final: drop the clips fetched during the game so the next refresh
        picks up the league's rewritten files. False when there is nothing to redo."""
        if self.rewritten or not self.fetched_in_play:
            return False
        self.rewritten = True
        self.clips.clear()
        self.attempts.clear()
        return True

    def pending(self, max_attempts: int) -> bool:
        """Still owes a clip: a goal without a file so far that has attempts left, or a goal
        the landing has not given a URL for yet."""
        goals = (self.last_value or {}).get("goals") or []
        if not goals:
            return True
        return any(g.get("event_id") not in self.clips and self.attempts.get(g.get("event_id"), 0) < max_attempts for g in goals)


# -- events: when a clip is worth interrupting for ---------------------------------------------


def detect_replays(prev: Snapshot, new: Snapshot) -> Iterable[Event]:
    """Diff the main event and the replays: a stoppage with clips in hand, an intermission,
    a final — and a clip arriving while any of those is already on.

    Payloads carry the game, the ids of the goals that have a clip (the board reads the
    clips themselves from the snapshot, so the event stays small enough for the MQTT
    bridge) and, for an intermission, the period that just ended. The board decides what
    is new to the viewer; a detector keeps no state."""
    a, b = prev.get(MAIN_EVENT), new.get(MAIN_EVENT)
    ra, rb = prev.get(REPLAYS), new.get(REPLAYS)
    if not b or not rb or rb.get("game_id") != b.get("id") or b.get("simulated"):
        return []
    clips = [g["event_id"] for g in rb.get("goals") or [] if g.get("clip") and g.get("event_id") is not None]
    if not clips:
        return []
    before = {g.get("event_id") for g in (ra or {}).get("goals") or [] if g.get("clip")} if ra and ra.get("game_id") == b.get("id") else set()
    arrived = any(ev not in before for ev in clips)
    same_game = bool(a) and a.get("id") == b.get("id")
    phase = b.get("phase")
    ts = max(new.updated.get(MAIN_EVENT, 0.0), new.updated.get(REPLAYS, 0.0))

    def event(kind: str, **extra: Any) -> Event:
        return Event(kind, ts=ts, payload={"game": b, "clips": clips, **extra})

    if phase == "live":
        stopped = not b.get("clock_running")
        whistle = same_game and a.get("phase") == "live" and a.get("clock_running") and stopped
        if whistle or (arrived and stopped):
            return [event("nhl.replay_stoppage")]
    elif phase == "intermission":
        began = not same_game or a.get("phase") != "intermission"
        if began or arrived:
            period = b.get("period_number") or max((g.get("period") or 0 for g in rb.get("goals") or [] if g.get("clip")), default=0)
            return [event("nhl.replay_intermission", period=period)]
    elif phase == "postgame" and (b.get("outcome") or "").startswith("FINAL"):
        began = not same_game or a.get("phase") != "postgame"
        if began or arrived:
            return [event("nhl.replay_final")]
    return []


def _abbrev(team: dict[str, Any]) -> str:
    value = team.get("abbrev")
    return value.get("default", "") if isinstance(value, dict) else (value or "")
