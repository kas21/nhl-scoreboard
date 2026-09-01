"""Goal and penalty event boards — ports of the old client's celebration animations.

Goal (favourite): scrolling band of [logo] GOAL! [logo] GOAL! ... over sweeping red
glow bars, text colour cycling team-primary/black, then a goal-summary card.
Goal (opponent): short primary/accent strobe.
Penalty: the referee GIF, then a penalty-summary card.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BoardContext, EventBoard, SequenceMixin
from ...data import Event
from ...render import Absolute, Sequence, Slide, Text, load_font, render_tree
from ...render.anim import gif_frames
from ...render.fx import Chip, outlined, stroked_text
from ..teams import logo, team

ASSETS = Path(__file__).parent.parent.parent / "assets"
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
LIGHT = (200, 200, 200)
PENALTY_YELLOW = (255, 196, 0)


class GoalConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Goal celebration")
    enabled: bool = True
    duration: float = Field(8.0, ge=2, le=30, description="Seconds of GOAL! animation (favourite goals)")
    summary: bool = Field(True, description="Follow with a scorer/assists card")
    summary_duration: float = Field(5.0, ge=2, le=15)
    opponent_goals: bool = Field(True, description="Flash briefly for opponent goals")
    opponent_flashes: int = Field(6, ge=1, le=20)


class PenaltyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Penalty alert")
    enabled: bool = True
    summary: bool = Field(True, description="Follow the referee animation with a details card")
    summary_duration: float = Field(5.0, ge=2, le=15)


# -- goal celebration frames --------------------------------------------------

@lru_cache(maxsize=8)
def _glow_bar(width: int, height: int, bottom: bool) -> Image.Image:
    img = Image.new("RGBA", (width, height), (255, 0, 0, 0))
    px = img.load()
    cx = (width - 1) / 2
    for y in range(height):
        vert = (y / height) if bottom else (1 - y / height)
        for x in range(width):
            horiz = 1 - (abs(x - cx) / cx) ** 1.5
            px[x, y] = (255, 0, 0, int(255 * max(vert * horiz, 0)))
    return img


def goal_frames(abbrev: str, width: int, height: int, seconds: float, fps: int) -> list[Image.Image]:
    """The old ``generate_goal_animation_frames``: scrolling logo/GOAL! band over sweeping glow bars."""
    t = team(abbrev)
    return celebration_frames("GOAL!", logo(abbrev, 128), t.primary, width, height, seconds, fps)


def celebration_frames(word: str, logo_img: Image.Image, primary: tuple[int, int, int], width: int, height: int,
                       seconds: float, fps: int) -> list[Image.Image]:
    """Scrolling [logo] WORD band over sweeping red glow bars, text alternating primary/black."""
    logo_h = int(height * 0.7)
    lg = logo_img.copy()
    lg.thumbnail((width, logo_h), Image.LANCZOS)
    lg = outlined(lg, (0, 0, 0, 255), radius=2)
    gothic = load_font("gothic", int(height * 0.625))               # 40 at 64 high
    words = [stroked_text(word, gothic, primary, WHITE, width=2, pad=4),
             stroked_text(word, gothic, BLACK, WHITE, width=2, pad=4)]
    gap = 20
    unit_w = lg.width + gap + words[0].width + gap
    band_h = max(lg.height, words[0].height)
    bands = []
    for word in words:
        band = Image.new("RGBA", (unit_w * 4, band_h), (0, 0, 0, 0))
        for i in range(4):
            x = i * unit_w
            band.alpha_composite(lg, (x, (band_h - lg.height) // 2))
            band.alpha_composite(word, (x + lg.width + gap, (band_h - word.height) // 2))
        bands.append(band)
    bar_w, bar_h = int(width * 0.9), 12
    top, bottom = _glow_bar(bar_w, bar_h, False), _glow_bar(bar_w, bar_h, True)
    band_y = (height - band_h) // 2
    frames = []
    n = int(seconds * fps)
    for f in range(n):
        frame = Image.new("RGBA", (width, height), (0, 0, 0, 255))
        gx = width - (f * 12) % (width + bar_w)
        frame.alpha_composite(top, (gx, 0)) if 0 <= gx < width else frame.paste(top, (gx, 0), top)
        frame.paste(bottom, (gx, height - bar_h), bottom)
        band = bands[(f // (fps // 2)) % 2]
        offset = (f * 2) % unit_w
        frame.paste(band, (-offset, band_y), band)
        frames.append(frame.convert("RGB"))
    return frames


def flash_frames(abbrev: str, width: int, height: int, count: int) -> list[Image.Image]:
    """Opponent goal: alternate primary / accent fills with black between (2 frames per flash)."""
    t = team(abbrev)
    black = Image.new("RGB", (width, height), BLACK)
    out = []
    for i in range(count):
        out.append(Image.new("RGB", (width, height), t.primary if i % 2 == 0 else t.accent))
        out.append(black)
    return out


# -- summary cards --------------------------------------------------------------

def _card(rows: list[tuple[object, int, int, int, int, float]], header: Image.Image, statics: list, width: int, height: int,
          seconds: float, fps: int) -> list[Image.Image]:
    """Rows slide up in (0.3s, staggered), hold, slide out. Header/statics are fixed."""
    frames = []
    n = int(seconds * fps)
    for f in range(n):
        t = f / fps
        items = [(_img(header), 0, 0, width, header.height), *[(s, x, y, w, h) for s, x, y, w, h in statics]]
        for node, x, y, w, h, delay in rows:
            if t < delay or t >= seconds - delay:
                continue
            if t < seconds - 0.3 - delay:
                items.append((Slide(node, 0.3, "up", delay=delay, h_align="start", v_align="start"), x, y, w, h))
            else:
                items.append((Slide(node, 0.3, "up", delay=seconds - 0.3 - delay, out=True, h_align="start", v_align="start"), x, y, w, h))
        frames.append(render_tree(Absolute(items), width, height, t=t))
    return frames


def _img(image: Image.Image):
    from ...render import Img
    return Img(image)


def goal_summary_frames(goal: dict[str, Any], abbrev: str, width: int, height: int, seconds: float, fps: int, f6) -> list[Image.Image]:
    t = team(abbrev)
    ari = load_font("ari", 11)
    header_txt = f"{abbrev} GOAL!  at {goal.get('time', '')}/{goal.get('period', '')}".rstrip("/ ")
    from ...render.fx import chip
    header = chip(header_txt, f6, t.text_on_primary, t.primary, pad=(1, 1, width, 1)).crop((0, 0, width, 7))
    first = (goal.get("first_name") or goal.get("scorer", "")).upper()
    last = (goal.get("last_name") or "").upper()
    if goal.get("goals_to_date"):
        last = f"{last}({goal['goals_to_date']})"
    rows = [
        (Text(f"#{goal['sweater']}" if goal.get("sweater") else "", ari, t.primary), 1, 9, 60, 9, 0.0),
        (Text(first, ari, WHITE), 1, 19, width - 1, 9, 0.1),
        (Text(last, ari, WHITE), 1, 29, width - 1, 11, 0.2),
    ]
    statics = [(Chip("ASSISTS", f6, t.text_on_primary, t.primary), 1, 44, 29, 7)]
    for i, a in enumerate((goal.get("assists") or [])[:2]):
        rows.append((Text(a, f6, LIGHT), 1, 52 + 6 * i, width - 1, 5, 0.1 * (3 + i)))
    rows = [(Anchor_start(n), x, y, w, h, d) for n, x, y, w, h, d in rows]
    return _card(rows, header, [(Anchor_start(n), x, y, w, h) for n, x, y, w, h in statics], width, height, seconds, fps)


def penalty_summary_frames(pen: dict[str, Any], abbrev: str, width: int, height: int, seconds: float, fps: int, f6) -> list[Image.Image]:
    t = team(abbrev)
    ari = load_font("ari", 11)
    from ...render.fx import chip
    header_txt = f"{abbrev} PENALTY!  at {pen.get('time', '')}/{pen.get('period', '')}".rstrip("/ ")
    header = chip(header_txt, f6, BLACK, PENALTY_YELLOW, pad=(1, 1, width, 1)).crop((0, 0, width, 7))
    player = (pen.get("player") or "").upper()
    first, _, last = player.partition(" ")
    kind = "MAJOR PENALTY" if int(pen.get("duration") or 0) >= 5 else "MINOR PENALTY"
    rows = [
        (Chip(abbrev, ari, t.text_on_primary, t.primary, pad=(1, 1, 1, 1)), 1, 9, 30, 11, 0.0),
        (Text(first, ari, WHITE), 1, 21, width - 1, 9, 0.1),
        (Text(last or "", ari, WHITE), 1, 31, width - 1, 9, 0.2),
        (Text(f"{pen.get('duration', 2)} MIN", f6, LIGHT), 1, 52, 60, 5, 0.3),
        (Text((pen.get("desc") or pen.get("type") or "").upper(), f6, LIGHT), 1, 58, width - 1, 5, 0.4),
    ]
    statics = [(Chip(kind, f6, BLACK, PENALTY_YELLOW), 1, 44, 60, 7)]
    rows = [(Anchor_start(n), x, y, w, h, d) for n, x, y, w, h, d in rows]
    return _card(rows, header, [(Anchor_start(n), x, y, w, h) for n, x, y, w, h in statics], width, height, seconds, fps)


def Anchor_start(node):
    from ...render import Anchor
    return Anchor(node, h="start", v="start")


@lru_cache(maxsize=4)
def penalty_gif_frames(width: int, height: int, slowdown: int = 3) -> tuple[Image.Image, ...]:
    src = gif_frames(str(ASSETS / "penalty_animation.gif"))
    if not src:
        return ()
    scale = min(width // src[0].width, height // src[0].height) or 1
    out = []
    for f in src:
        img = f.resize((f.width * scale, f.height * scale), Image.NEAREST)
        canvas = Image.new("RGB", (width, height), BLACK)
        canvas.paste(img, ((width - img.width) // 2, (height - img.height) // 2))
        out.extend([canvas] * slowdown)
    return tuple(out)


# -- boards ----------------------------------------------------------------------

class GoalBoard(SequenceMixin, EventBoard):
    key = "nhl.goal"
    title = "Goal celebration"
    config_model = GoalConfig
    event_kinds = frozenset({"nhl.goal"})

    def matches(self, event: Event, cfg: GoalConfig) -> bool:
        if not cfg.enabled or event.kind not in self.event_kinds:
            return False
        game = event.payload.get("game") or {}
        return game.get("favorite_side") == event.payload.get("side") or cfg.opponent_goals

    def build(self, ctx: BoardContext, cfg: GoalConfig) -> Sequence:
        ev = ctx.event
        payload: dict[str, Any] = ev.payload if ev else {}
        game = payload.get("game") or {}
        side = payload.get("side", "away")
        abbrev = (ev.team if ev and ev.team else game.get(side, {}).get("abbrev", "")) or ""
        seq = Sequence(ctx.fps)
        if game.get("favorite_side") != side:
            return seq.frames(flash_frames(abbrev, ctx.width, ctx.height, cfg.opponent_flashes)).build(Image.new("RGB", (ctx.width, ctx.height)))
        seq.frames(goal_frames(abbrev, ctx.width, ctx.height, cfg.duration, ctx.fps))
        goal = payload.get("goal")
        if cfg.summary and goal:
            seq.frames(goal_summary_frames(goal, abbrev, ctx.width, ctx.height, cfg.summary_duration, ctx.fps, ctx.profile.label_font()))
        return seq.build(Image.new("RGB", (ctx.width, ctx.height)))


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
        seq = Sequence(ctx.fps).frames(list(penalty_gif_frames(ctx.width, ctx.height)))
        if cfg.summary:
            seq.frames(penalty_summary_frames(pen, abbrev, ctx.width, ctx.height, cfg.summary_duration, ctx.fps, ctx.profile.label_font()))
        return seq.build(Image.new("RGB", (ctx.width, ctx.height)))
