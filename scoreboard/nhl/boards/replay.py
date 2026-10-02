"""Goal replay boards: the league's tracking clip of each goal, as dots on a rink.

What Game Center animates after a goal — twelve players and the puck for the fourteen
seconds before it went in — drawn the way an LED panel can: the rink in outline, a dot
per player in the team's colour, the scorer blinking, the puck white with a short tail.
A header chip names the team, the scorer and the score after the goal.

Two boards share the drawing. ``GoalReplayBoard`` sits in a playlist and plays every goal
of the game in order (the postgame rotation, or last night's goals the next morning).
``GoalReplayAlert`` interrupts: at the next whistle after a goal's clip arrives (every clip
not yet shown that way), at the intermission with the period's goals, and at the final
with the whole game — each on its own switch. A clip runs at its own pace unless the
playlist, or the alert's setting, gives seconds per goal, in which case the clip is
stretched or squeezed to fit.
"""
from __future__ import annotations

from bisect import bisect_right
from functools import lru_cache
from typing import Any, ClassVar

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext, EventBoard, per_item
from ...data import Event
from ...render import Text, load_font, render_tree
from ...render.fx import chip
from ..replay import RINK_LENGTH_IN, RINK_WIDTH_IN
from ..teams import team

RGB = tuple[int, int, int]
WHITE = (255, 255, 255)
BOARDS = (110, 110, 110)
ICE = (0, 0, 0)
CENTRE_RED = (150, 0, 0)
BLUE_LINE = (0, 0, 170)
GOAL_LINE = (120, 0, 0)
CREASE = (0, 90, 150)
TRAIL = ((160, 160, 160), (110, 110, 110), (70, 70, 70), (40, 40, 40))
HEADER_H = 7
RINK_FT = (200, 85)
BLINK_SECONDS = 0.3
STOPPAGE, INTERMISSION, FINAL = "nhl.replay_stoppage", "nhl.replay_intermission", "nhl.replay_final"


class ReplayConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="NHL goal replay")
    seconds_per_goal: float = Field(0.0, ge=0, le=60, description="Seconds each goal plays (0: the clip's own length, about 14 s); a playlist entry's seconds override it")
    hold_seconds: float = Field(1.5, ge=0, le=5, description="Freeze on the goal this long before the next one")
    favorite_goals_only: bool = Field(False, description="Replay your team's goals only")
    puck_trail: bool = Field(True, description="Leave a short tail behind the puck")


class ReplayAlertConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="NHL goal replay (interrupt)")
    enabled: bool = True
    at_stoppage: bool = Field(True, description="At the next whistle after a goal's replay arrives (three to four minutes after the goal), play every replay not yet shown this way")
    at_intermission: bool = Field(True, description="At the intermission, play every goal of the period that just ended")
    at_final: bool = Field(False, description="At the final, play every goal of the game (the playlist board in the postgame rotation does this too)")
    favorite_goals_only: bool = Field(False, description="Replay your team's goals only")
    seconds_per_goal: float = Field(0.0, ge=0, le=60, description="Seconds each goal plays (0: the clip's own length, about 14 s)")
    hold_seconds: float = Field(1.5, ge=0, le=5, description="Freeze on the goal this long before the next one")
    puck_trail: bool = Field(True, description="Leave a short tail behind the puck")


# -- the rink -----------------------------------------------------------------------------


class Rink:
    """Where the rink sits on the panel and how to get from inches to pixels."""

    def __init__(self, width: int, height: int) -> None:
        avail_w, avail_h = width - 2, height - HEADER_H - 2
        self.scale = max(min(avail_w / RINK_FT[0], avail_h / RINK_FT[1]), 0.05)      # px per foot
        self.w, self.h = max(int(RINK_FT[0] * self.scale), 2), max(int(RINK_FT[1] * self.scale), 2)
        self.x = (width - self.w) // 2
        self.y = HEADER_H + 1 + (avail_h - self.h) // 2
        self.dot = 3 if self.scale >= 1.2 else 2 if self.scale >= 0.45 else 1

    def px(self, x_in: float, y_in: float) -> tuple[int, int]:
        fx = min(max(x_in / RINK_LENGTH_IN, 0.0), 1.0)
        fy = min(max(y_in / RINK_WIDTH_IN, 0.0), 1.0)
        return self.x + int(fx * (self.w - 1)), self.y + int(fy * (self.h - 1))


@lru_cache(maxsize=8)
def rink_image(width: int, height: int) -> Image.Image:
    """The empty rink: boards, centre line, blue lines, goal lines, creases, face-off dots."""
    r = Rink(width, height)
    img = Image.new("RGB", (width, height), (0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = int(28 * r.scale)
    box = (r.x, r.y, r.x + r.w - 1, r.y + r.h - 1)
    if radius >= 2:
        d.rounded_rectangle(box, radius=radius, outline=BOARDS, fill=ICE)
    else:
        d.rectangle(box, outline=BOARDS, fill=ICE)
    top, bottom = r.y + 1, r.y + r.h - 2

    def vline(feet: float, color: RGB) -> None:
        x = r.x + int(feet * r.scale)
        d.line((x, top, x, bottom), fill=color)

    for feet in (11, 189):
        vline(feet, GOAL_LINE)
    for feet in (75, 125):
        vline(feet, BLUE_LINE)
    vline(100, CENTRE_RED)
    crease = int(6 * r.scale)
    if crease >= 2:
        for feet, side in ((11, 1), (189, -1)):
            x, cy = r.x + int(feet * r.scale), r.y + r.h // 2
            bbox = (x - crease, cy - crease, x + crease, cy + crease)
            d.pieslice(bbox, 270, 90, outline=CREASE) if side > 0 else d.pieslice(bbox, 90, 270, outline=CREASE)
            d.line((x, cy - crease, x, cy + crease), fill=GOAL_LINE)
    if r.scale >= 0.5:
        for feet_x in (31, 169):
            for feet_y in (20.5, 64.5):
                d.point((r.x + int(feet_x * r.scale), r.y + int(feet_y * r.scale)), fill=CENTRE_RED)
        d.point((r.x + int(100 * r.scale), r.y + int(42.5 * r.scale)), fill=BLUE_LINE)
    return img


def side_colors(away: str, home: str) -> tuple[RGB, RGB]:
    """A colour per side, the home side falling back to its accent when the two primaries
    would be hard to tell apart on a panel."""
    a, h = team(away), team(home)
    if sum(abs(x - y) for x, y in zip(a.primary, h.primary)) < 150:
        return a.primary, h.accent
    return a.primary, h.primary


@lru_cache(maxsize=16)
def header_image(width: int, abbrev: str, name: str, score: str) -> Image.Image:
    """``[TEAM] LASTNAME        1-0``: the chip in the team's colours, the score at the right."""
    t = team(abbrev)
    font = load_font("pixel", 6)
    img = Image.new("RGB", (width, HEADER_H), (0, 0, 0))
    tag = chip(abbrev, font, t.text_on_primary, t.primary, pad=(1, 1, 1, 1))
    img.paste(tag, (0, 0))
    score_img = render_tree(Text(score, font, WHITE), *_measure(score, font), t=0)
    img.paste(score_img, (width - score_img.width - 1, 1))
    room = width - tag.width - score_img.width - 6
    label = name
    while label and _measure(label, font)[0] > room:
        label = label[:-1]
    if label:
        text_img = render_tree(Text(label, font, WHITE), *_measure(label, font), t=0)
        img.paste(text_img, (tag.width + 2, 1))
    return img


def _measure(text: str, font) -> tuple[int, int]:
    w, h = Text(text, font, WHITE).measure()
    return max(w, 1), max(h, 1)


# -- playing a list of goals -------------------------------------------------------------


Timeline = list[tuple[dict[str, Any], float, float]]       # (goal, seconds the clip plays, playback speed)


def with_clips(value: dict[str, Any] | None, favorite_only: bool = False) -> list[dict[str, Any]]:
    """The goals of a published recap that have something to draw, in game order."""
    value = value or {}
    goals = [g for g in value.get("goals") or [] if g.get("clip") and g["clip"].get("frames")]
    if favorite_only and value.get("favorite"):
        goals = [g for g in goals if g.get("team") == value["favorite"]]
    return goals


def timeline(goals: list[dict[str, Any]], seconds_per_goal: float, hold: float) -> Timeline:
    out: Timeline = []
    for g in goals:
        clip = g["clip"]
        natural = max((clip["frames"][-1]["t"] + 1) / max(clip.get("fps") or 10, 1), 0.5)
        play = natural if seconds_per_goal <= 0 else max(seconds_per_goal - hold, 0.5)
        out.append((g, play, natural / play))
    return out


def total_seconds(line: Timeline, hold: float) -> float:
    return sum(play + hold for _, play, _ in line)


def frame_at(ctx: BoardContext, away: str, home: str, line: Timeline, hold: float, puck_trail: bool) -> Image.Image:
    """The frame for ``ctx.elapsed`` into a timeline: black once it has run out."""
    t = ctx.elapsed
    for goal, play, speed in line:
        if t < play + hold:
            return goal_frame(ctx, away, home, goal, min(t, play) * speed, holding=t >= play, puck_trail=puck_trail)
        t -= play + hold
    return Image.new("RGB", (ctx.width, ctx.height), (0, 0, 0))


def goal_frame(ctx: BoardContext, away: str, home: str, goal: dict[str, Any], clip_t: float, holding: bool,
               puck_trail: bool) -> Image.Image:
    clip = goal["clip"]
    frames = clip["frames"]
    fps = max(clip.get("fps") or 10, 1)
    times = [f["t"] for f in frames]
    i = max(bisect_right(times, int(clip_t * fps)) - 1, 0)
    img = rink_image(ctx.width, ctx.height).copy()
    r = Rink(ctx.width, ctx.height)
    d = ImageDraw.Draw(img)
    away_c, home_c = side_colors(away, home)
    players = clip["players"]
    blink_on = int(ctx.elapsed / BLINK_SECONDS) % 2 == 0
    for idx, x, y in frames[i]["on_ice"]:
        p = players[idx] if 0 <= idx < len(players) else {}
        color = away_c if p.get("side") == "away" else home_c
        scorer = goal.get("scorer_id") is not None and p.get("player_id") == goal.get("scorer_id")
        if scorer and (blink_on or holding):
            color = WHITE
        _dot(d, r.px(x, y), r.dot, color)
    if puck_trail:
        for back, shade in enumerate(TRAIL, start=1):
            j = i - back * 2
            if j < 0:
                break
            puck = frames[j].get("puck")
            if puck:
                d.point(r.px(*puck), fill=shade)
    puck = frames[i].get("puck")
    if puck and (not holding or blink_on):
        _dot(d, r.px(*puck), max(r.dot - 1, 1), WHITE)
    header = header_image(ctx.width, goal.get("team", ""), (goal.get("last_name") or goal.get("scorer") or "").upper(),
                          f"{goal.get('away_score', 0)}-{goal.get('home_score', 0)}")
    img.paste(header, (0, 0))
    return img


def _dot(d: ImageDraw.ImageDraw, at: tuple[int, int], size: int, color: RGB) -> None:
    x, y = at
    if size <= 1:
        d.point((x, y), fill=color)
    else:
        half = size // 2
        d.rectangle((x - half, y - half, x - half + size - 1, y - half + size - 1), fill=color)


# -- boards -------------------------------------------------------------------------------


class GoalReplayBoard(BaseBoard):
    """Every goal of the recap, in order; a playlist board."""

    key = "nhl.goal_replay"
    title = "NHL goal replay"
    config_model = ReplayConfig
    requires = frozenset({"nhl.goal_replays"})
    pace_unit = "goal"

    def goals(self, ctx: BoardContext, cfg: ReplayConfig) -> list[dict[str, Any]]:
        return with_clips(ctx.snapshot.get("nhl.goal_replays"), cfg.favorite_goals_only)

    def _timeline(self, ctx: BoardContext, cfg: ReplayConfig) -> Timeline:
        return timeline(self.goals(ctx, cfg), per_item(ctx, cfg.seconds_per_goal), cfg.hold_seconds)

    def auto_seconds(self, ctx: BoardContext, cfg: ReplayConfig) -> float | None:
        return total_seconds(self._timeline(ctx, cfg), cfg.hold_seconds) or None

    def auto_items(self, ctx: BoardContext, cfg: ReplayConfig) -> tuple[int, str] | None:
        n = len(self.goals(ctx, cfg))
        return (n, "goal") if n else None

    def done(self, ctx: BoardContext, cfg: ReplayConfig) -> bool:
        total = self.auto_seconds(ctx, cfg)
        return total is None or ctx.elapsed >= total

    def render(self, ctx: BoardContext, cfg: ReplayConfig) -> Image.Image:
        value = ctx.snapshot.get("nhl.goal_replays") or {}
        return frame_at(ctx, value.get("away", {}).get("abbrev", ""), value.get("home", {}).get("abbrev", ""),
                        self._timeline(ctx, cfg), cfg.hold_seconds, cfg.puck_trail)


class GoalReplayAlert(EventBoard):
    """The replays the viewer has not seen yet, at the moment the game pauses.

    Three occasions, three switches. A whistle plays every clip not yet shown at a whistle
    (one goal, usually; two or three when the files of a burst land together). An intermission
    plays the period's goals, whistle or not; a clip for a late goal that arrives during the
    intermission plays by itself when it comes. The final plays the whole game the same way.
    What has been shown is remembered per game and occasion on the board itself — the
    detector is a pure diff and cannot know."""

    key = "nhl.goal_replay_alert"
    title = "NHL goal replay (interrupt)"
    config_model = ReplayAlertConfig
    event_kinds = frozenset({STOPPAGE, INTERMISSION, FINAL})
    max_seconds: ClassVar[float | None] = 240.0

    def __init__(self) -> None:
        self._shown: dict[tuple[int, str], set[int]] = {}
        self._playing: list[dict[str, Any]] = []
        self._line: Timeline = []
        self._sides: tuple[str, str] = ("", "")
        self._entered: Event | None = None

    # -- what is new to the viewer --

    @staticmethod
    def _occasion(event: Event) -> str:
        if event.kind == INTERMISSION:
            return f"intermission:{event.payload.get('period', 0)}"
        return "final" if event.kind == FINAL else "stoppage"

    def _unshown(self, event: Event, cfg: ReplayAlertConfig) -> list[int]:
        game = event.payload.get("game") or {}
        key = (game.get("id"), self._occasion(event))
        seen = self._shown.get(key, set())
        return [ev for ev in event.payload.get("clips") or [] if ev not in seen]

    def matches(self, event: Event, cfg: ReplayAlertConfig) -> bool:
        if not cfg.enabled or event.kind not in self.event_kinds:
            return False
        wanted = {STOPPAGE: cfg.at_stoppage, INTERMISSION: cfg.at_intermission, FINAL: cfg.at_final}[event.kind]
        return wanted and bool(self._unshown(event, cfg))

    def _select(self, ctx: BoardContext, event: Event, cfg: ReplayAlertConfig) -> list[dict[str, Any]]:
        """The clips this run plays, from the snapshot (the event carries ids only)."""
        value = ctx.snapshot.get("nhl.goal_replays") or {}
        game = event.payload.get("game") or {}
        if value.get("game_id") != game.get("id"):
            return []
        ids = set(self._unshown(event, cfg))
        goals = [g for g in with_clips(value, cfg.favorite_goals_only) if g.get("event_id") in ids]
        if event.kind == INTERMISSION:
            period = event.payload.get("period")
            goals = [g for g in goals if not period or g.get("period") == period]
        return goals

    def enter(self, ctx: BoardContext, cfg: ReplayAlertConfig) -> None:
        event = ctx.event
        self._entered = event
        self._playing, self._line, self._sides = [], [], ("", "")
        if event is None:
            return
        game = event.payload.get("game") or {}
        self._playing = self._select(ctx, event, cfg)
        key = (game.get("id"), self._occasion(event))
        # Everything the event offered counts as shown, the filtered-out ones included: an
        # opponent's goal kept off by favorite_goals_only must not re-arm the next whistle.
        self._shown.setdefault(key, set()).update(event.payload.get("clips") or [])
        self._line = timeline(self._playing, cfg.seconds_per_goal, cfg.hold_seconds)
        self._sides = ((game.get("away") or {}).get("abbrev", ""), (game.get("home") or {}).get("abbrev", ""))

    def render(self, ctx: BoardContext, cfg: ReplayAlertConfig) -> Image.Image:
        if ctx.event is not self._entered:
            self.enter(ctx, cfg)
        return frame_at(ctx, *self._sides, self._line, cfg.hold_seconds, cfg.puck_trail)

    def done(self, ctx: BoardContext, cfg: ReplayAlertConfig) -> bool:
        if ctx.event is not self._entered:
            self.enter(ctx, cfg)
        return ctx.elapsed >= total_seconds(self._line, cfg.hold_seconds)

    def auto_seconds(self, ctx: BoardContext, cfg: ReplayAlertConfig) -> float | None:
        return total_seconds(self._line, cfg.hold_seconds) or None
