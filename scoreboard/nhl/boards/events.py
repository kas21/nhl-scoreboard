"""Goal and penalty event boards: one Sequence each, built on entry."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BoardContext, EventBoard, SequenceMixin
from ...data import Event
from ...render import (
    HBox,
    Img,
    Sequence,
    Spacer,
    Text,
    VBox,
    fit_font,
    load_font,
    render_tree,
    text_size,
)
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


def _fit_rows(rows: list, height: int, spacing: int = 1) -> list:
    """Drop trailing optional rows until the column fits."""
    while len(rows) > 1 and VBox(rows, spacing=spacing).measure()[1] > height:
        rows.pop()
    return rows


class GoalBoard(SequenceMixin, EventBoard):
    key = "nhl.goal"
    title = "Goal celebration"
    config_model = GoalConfig
    event_kinds = frozenset({"nhl.goal"})

    def matches(self, event: Event, cfg: GoalConfig) -> bool:
        if not cfg.enabled or event.kind not in self.event_kinds:
            return False
        game = event.payload.get("game") or {}
        is_fav = game.get("favorite_side") == event.payload.get("side")
        return is_fav or cfg.opponent_goals

    def build(self, ctx: BoardContext, cfg: GoalConfig) -> Sequence:
        ev = ctx.event
        payload: dict[str, Any] = ev.payload if ev else {}
        game = payload.get("game") or {}
        side = payload.get("side", "away")
        abbrev = (ev.team if ev and ev.team else game.get(side, {}).get("abbrev", "")) or ""
        t = team(abbrev)
        if game.get("favorite_side") != side:                       # opponent: short flash only
            black = render_tree(Spacer(), ctx.width, ctx.height)
            return Sequence(ctx.fps).flash(t.primary, times=3, secs=cfg.opponent_duration).build(black)
        p = ctx.profile
        usable = ctx.width - 2 * p.pad
        rows = [HBox([Img(logo(abbrev, p.logo_small)), Text("GOAL!", fit_font("GOAL!", "block", usable - p.logo_small - p.pad, p.font_large), t.accent)], spacing=p.pad)]
        goal = payload.get("goal") if cfg.show_scorer else None
        if goal:
            who = goal.get("scorer") or ""
            label = f"{who} ({goal['goals_to_date']})" if goal.get("goals_to_date") else who
            rows.append(Text(label, fit_font(label, "pixel", usable, p.font_medium), WHITE))
            if goal.get("assists"):
                assists = ", ".join(goal["assists"])
                rows.append(Text(assists, fit_font(assists, "pixel", usable, p.font_small), GREY))
        rows.append(Text(payload.get("score", ""), load_font("pixel", p.font_small), GREY))
        still = render_tree(VBox([Spacer(), *_fit_rows(rows, ctx.height), Spacer()], spacing=1), ctx.width, ctx.height, background=t.primary)
        intro, outro = 0.9, 0.5
        return (Sequence(ctx.fps).flash(t.primary, times=2, secs=0.4).slide_in("right", 0.5)
                .hold(max(cfg.duration - intro - outro, 0.5)).fade_out(outro).build(still))


class PenaltyBoard(SequenceMixin, EventBoard):
    key = "nhl.penalty"
    title = "Penalty alert"
    config_model = PenaltyConfig
    event_kinds = frozenset({"nhl.penalty"})

    def matches(self, event: Event, cfg: PenaltyConfig) -> bool:
        return cfg.enabled and event.kind in self.event_kinds

    def build(self, ctx: BoardContext, cfg: PenaltyConfig) -> Sequence:
        ev = ctx.event
        pen = (ev.payload.get("penalty") if ev else None) or {}
        abbrev = pen.get("team") or (ev.team if ev else "") or ""
        t = team(abbrev)
        p = ctx.profile
        usable = ctx.width - 2 * p.pad
        desc = (pen.get("desc") or pen.get("type") or "penalty").upper()
        if text_size(desc, fit_font(desc, "pixel", usable, p.font_medium))[0] > usable:      # can't fit at any size
            desc = (pen.get("type") or "PENALTY").upper()
        rows = [
            HBox([Img(logo(abbrev, p.logo_small)), Text("PENALTY", fit_font("PENALTY", "block", usable - p.logo_small - p.pad, p.font_medium), WHITE)], spacing=p.pad),
            Text(desc, fit_font(desc, "pixel", usable, p.font_medium), (255, 220, 0)),
        ]
        who, dur = pen.get("player") or "", f"{pen.get('duration')} MIN" if pen.get("duration") else ""
        line = " ".join(x for x in (who, dur) if x)
        if line and text_size(line, fit_font(line, "pixel", usable, p.font_small))[0] <= usable:
            rows.append(Text(line, fit_font(line, "pixel", usable, p.font_small), GREY))
        elif dur:
            rows.append(Text(dur, load_font("pixel", p.font_small), GREY))
        bg = tuple(int(c * 0.35) for c in t.primary)
        still = render_tree(VBox([Spacer(), *_fit_rows(rows, ctx.height), Spacer()], spacing=1), ctx.width, ctx.height, background=bg)
        return Sequence(ctx.fps).slide_in("up", 0.35).hold(cfg.duration).build(still)
