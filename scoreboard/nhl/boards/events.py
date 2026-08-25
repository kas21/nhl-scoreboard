"""Goal and penalty event boards (pre-rendered on entry)."""
from __future__ import annotations

from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BoardContext, EventBoard
from ...data import Event
from ...render import Spacer, Text, VBox, load_font, render_tree
from ...render.anim import fade, flash, frame_at, hold, slide_in
from ...render.layout import HBox, Img
from ...render.text import fit_font, text_size
from ..teams import logo, team
from .common import GREY, WHITE


class GoalConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Goal celebration")
    enabled: bool = True
    duration: float = Field(8.0, ge=2, le=30, description="Seconds for favourite-team goals")
    opponent_goals: bool = Field(True, description="Also flash briefly for opponent goals")
    opponent_duration: float = Field(2.0, ge=1, le=10)
    show_scorer: bool = True


class PenaltyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Penalty alert")
    enabled: bool = True
    duration: float = Field(5.0, ge=2, le=20)


def _favorite_side(game: dict[str, Any], snapshot) -> str | None:
    """Which side (if any) is a favourite — favourites live in the nhl source config, mirrored on the game."""
    fav = game.get("favorite_side")
    return fav


class GoalBoard(EventBoard):
    key = "nhl.goal"
    title = "Goal celebration"
    config_model = GoalConfig
    event_kinds = frozenset({"nhl.goal"})

    def __init__(self) -> None:
        self._frames: list[Image.Image] = []
        self._length = 0.0

    def matches(self, event: Event, cfg: GoalConfig) -> bool:
        if not cfg.enabled or event.kind not in self.event_kinds:
            return False
        game = event.payload.get("game") or {}
        is_fav = game.get("favorite_side") == event.payload.get("side")
        return is_fav or cfg.opponent_goals

    def enter(self, ctx: BoardContext, cfg: GoalConfig) -> None:
        ev = ctx.event
        game = (ev.payload.get("game") if ev else None) or {}
        side = ev.payload.get("side", "away") if ev else "away"
        abbrev = ev.team if ev and ev.team else game.get(side, {}).get("abbrev", "")
        is_fav = game.get("favorite_side") == side
        t = team(abbrev)
        fps = ctx.fps
        p = ctx.profile
        if not is_fav:
            base = Image.new("RGB", (ctx.width, ctx.height), (0, 0, 0))
            self._frames = flash(base, t.primary, count=3, frames_per_flash=max(fps // 6, 1))
            self._length = cfg.opponent_duration
            self.__done_at = len(self._frames) / fps
            return
        goal_word = Text("GOAL!", fit_font("GOAL!", "block", ctx.width - 2 * p.pad, p.font_large), t.accent)
        rows = [HBox([Img(logo(abbrev, p.logo_small)), goal_word], spacing=p.pad)]
        goal = ev.payload.get("goal") if ev else None
        if cfg.show_scorer and goal:
            who = goal.get("scorer") or ""
            label = f"{who} ({goal['goals_to_date']})" if goal.get("goals_to_date") else who
            rows.append(Text(label, fit_font(label, "pixel", ctx.width - 2 * p.pad, p.font_medium), WHITE))
            if goal.get("assists"):
                rows.append(Text(", ".join(goal["assists"]), fit_font(", ".join(goal["assists"]), "pixel", ctx.width - 2 * p.pad, p.font_small), GREY))
        rows.append(Text(ev.payload.get("score", "") if ev else "", load_font("pixel", p.font_small), GREY))
        while len(rows) > 1 and VBox(rows, spacing=1).measure()[1] > ctx.height:
            rows.pop()
        still = render_tree(VBox([Spacer(), *rows, Spacer()], spacing=1), ctx.width, ctx.height, background=t.primary)
        intro = flash(Image.new("RGB", still.size, (0, 0, 0)), t.primary, count=2, frames_per_flash=max(fps // 8, 1))
        intro += slide_in(still, frames=fps // 2, direction="right")
        outro = fade(still, fps // 2, 1.0, 0.0)
        hold_frames = max(int(fps * cfg.duration) - len(intro) - len(outro), 1)
        self._frames = intro + hold(still, hold_frames) + outro
        self._length = cfg.duration

    def render(self, ctx: BoardContext, cfg: GoalConfig) -> Image.Image:
        if not self._frames:
            self.enter(ctx, cfg)
        return frame_at(self._frames, ctx.elapsed, ctx.fps)

    def done(self, ctx: BoardContext, cfg: GoalConfig) -> bool:
        return ctx.elapsed >= len(self._frames) / max(ctx.fps, 1)


class PenaltyBoard(EventBoard):
    key = "nhl.penalty"
    title = "Penalty alert"
    config_model = PenaltyConfig
    event_kinds = frozenset({"nhl.penalty"})

    def __init__(self) -> None:
        self._frames: list[Image.Image] = []

    def matches(self, event: Event, cfg: PenaltyConfig) -> bool:
        return cfg.enabled and event.kind in self.event_kinds

    def enter(self, ctx: BoardContext, cfg: PenaltyConfig) -> None:
        ev = ctx.event
        pen = (ev.payload.get("penalty") if ev else None) or {}
        abbrev = pen.get("team") or (ev.team if ev else "") or ""
        t = team(abbrev)
        p = ctx.profile
        usable = ctx.width - 2 * p.pad
        desc = (pen.get("desc") or pen.get("type") or "penalty").upper()
        if text_size(desc, fit_font(desc, "pixel", usable, p.font_medium))[0] > usable:   # can't fit at any size
            desc = (pen.get("type") or "PENALTY").upper()
        rows = [
            Spacer(),
            HBox([Img(logo(abbrev, p.logo)), Text("PENALTY", fit_font("PENALTY", "block", usable - p.logo - p.pad, p.font_medium), (255, 255, 255))], spacing=p.pad),
            Text(desc, fit_font(desc, "pixel", usable, p.font_medium), (255, 220, 0)),
        ]
        who = pen.get("player") or ""
        dur = f"{pen.get('duration')} MIN" if pen.get("duration") else ""
        line = " ".join(x for x in (who, dur) if x)
        if line and text_size(line, fit_font(line, "pixel", usable, p.font_small))[0] <= usable:
            rows.append(Text(line, fit_font(line, "pixel", usable, p.font_small), GREY))
        elif dur:
            rows.append(Text(dur, load_font("pixel", p.font_small), GREY))
        rows.append(Spacer())
        still = render_tree(VBox(rows, spacing=1), ctx.width, ctx.height, background=tuple(int(c * 0.35) for c in t.primary))
        fps = ctx.fps
        self._frames = slide_in(still, fps // 3, "up") + hold(still, int(fps * cfg.duration))

    def render(self, ctx: BoardContext, cfg: PenaltyConfig) -> Image.Image:
        if not self._frames:
            self.enter(ctx, cfg)
        return frame_at(self._frames, ctx.elapsed, ctx.fps)

    def done(self, ctx: BoardContext, cfg: PenaltyConfig) -> bool:
        return ctx.elapsed >= len(self._frames) / max(ctx.fps, 1)
