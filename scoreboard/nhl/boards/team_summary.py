"""Team summary — port of the old board: dark gradient column, cascading text rows
(RECORD / LAST / NEXT sections), big logo sliding in from the right with a looping sheen,
scroll if needed, hold, exit upward."""
from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext
from ...render import Img, Sheen, render_node
from ...render.anim import quintic_out
from ...render.fx import chip, fit_logo, reflected_gradient
from ..teams import logo, team
from .common import fmt_date, fmt_time, local_time

WHITE = (255, 255, 255)
GREEN = (0, 255, 0)
RED = (255, 0, 0)
FADE_START, FADE_END = 46, 72     # header bars are solid to FADE_START, transparent by FADE_END (logo starts ~64)
CASCADE_FRAMES = 4
ROW_SLIDE_FRAMES = 4
SCROLL_DELAY = 5.0
EXIT_PX_PER_FRAME = 3


def _fade_mask(width: int, height: int, solid_until: int, gone_at: int) -> Image.Image:
    mask = Image.new("L", (width, height), 0)
    px = mask.load()
    for x in range(width):
        if x <= solid_until:
            a = 255
        elif x >= gone_at:
            a = 0
        else:
            a = int(255 * (1 - (x - solid_until) / (gone_at - solid_until)))
        for y in range(height):
            px[x, y] = a
    return mask


class TeamSummaryConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Team summary")
    scroll_speed: float = Field(5.0, ge=1, le=40)
    hold_seconds: float = Field(5.0, ge=0, le=20)
    sheen_seconds: float = Field(2.5, ge=0.5, le=10, description="Seconds per shimmer sweep across the logo")
    time_24h: bool = False


class TeamSummaryBoard(BaseBoard):
    key = "nhl.team_summary"
    title = "Team summary"
    config_model = TeamSummaryConfig
    requires = frozenset({"nhl.team_summary"})
    summary_key = "nhl.team_summary"

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        t = team(abbrev)
        return t.primary, t.text_on_primary

    def _record_lines(self, rec: dict[str, Any]) -> list[str]:
        """Text lines under the RECORD header (sport-specific)."""
        return [f"{rec['wins']}-{rec['losses']}-{rec['otl']}  {rec['points']} PTS",
                f"GP {rec['gp']}  L10 {'-'.join(map(str, rec['l10']))}"]

    def __init__(self) -> None:
        self._teams: list[dict[str, Any]] = []
        self._built: dict[str, tuple[Image.Image, list[tuple[int, bool]], Image.Image]] = {}
        self._timeline: list[float] = []
        self._size = (0, 0)

    def enter(self, ctx: BoardContext, cfg: TeamSummaryConfig) -> None:
        self._teams = list((ctx.snapshot.get(self.summary_key) or {}).values())
        self._built = {}
        self._size = (ctx.width, ctx.height)
        self._timeline = [self._seconds(s, ctx, cfg) for s in self._teams]

    # -- content --------------------------------------------------------------------

    def _rows(self, s: dict[str, Any], ctx: BoardContext, cfg: TeamSummaryConfig) -> list[tuple[Image.Image, bool]]:
        f6 = ctx.profile.label_font()
        primary, fg = self.team_colors(s["abbrev"])
        w = ctx.width
        rec = s["record"]

        def header(text: str) -> tuple[Image.Image, bool]:
            """Section bar in team colour, fading out before the logo so it never cuts through it."""
            bar = chip(text, f6, fg, primary, pad=(1, 1, w, 1)).crop((0, 0, w, 7))
            bar.putalpha(_fade_mask(w, 7, solid_until=FADE_START, gone_at=FADE_END))
            return bar, False

        def line(parts: list[tuple[str, tuple[int, int, int]]]) -> tuple[Image.Image, bool]:
            img = Image.new("RGBA", (w, 6), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            x = 0
            for text, color in parts:
                d.text((x, 0), text, font=f6, fill=color)
                x += d.textlength(text, font=f6) + 4
            return img, True

        streak = rec.get("streak", "")
        streak_color = GREEN if streak.startswith("W") else RED if streak.startswith("L") else WHITE
        rows = [header("RECORD")]
        rows += [line([(txt, WHITE)]) for txt in self._record_lines(rec)]
        rows += [line([("STREAK", WHITE), (streak, streak_color)]), header("LAST")]
        prev, nxt = s.get("prev_game"), s.get("next_game")
        if prev:
            rows.append(line([(f"{fmt_date(prev['date'])} {'VS' if prev['home'] else 'AT'} {prev['opponent']}", WHITE)]))
            rows.append(line([(prev["result"], GREEN if prev["result"] == "W" else RED), (f"{prev['score']}-{prev['opponent_score']}", WHITE)]))
        else:
            rows.append(line([("---------", WHITE)]))
        rows.append(header("NEXT"))
        if nxt:
            start = local_time(nxt["start_time_utc"], ctx.now.tzinfo)
            rows.append(line([(f"{fmt_date(nxt['date'])} {'VS' if nxt['home'] else 'AT'} {nxt['opponent']}", WHITE)]))
            rows.append(line([(fmt_time(start, cfg.time_24h).upper(), WHITE)]))
        else:
            rows.append(line([("---------", WHITE)]))
        return rows

    def _build(self, s: dict[str, Any], ctx: BoardContext, cfg: TeamSummaryConfig):
        rows = self._rows(s, ctx, cfg)
        total = sum(r.height for r, _ in rows) + max(len(rows) - 1, 0)
        comp = Image.new("RGBA", (ctx.width, max(total, ctx.height)), (0, 0, 0, 0))
        y, meta = 0, []
        for img, animated in rows:
            comp.alpha_composite(img, (0, y))
            meta.append((y, animated, img.height))
            y += img.height + 1
        lg = fit_logo(self.logo_image(s["abbrev"]), int(ctx.width * 0.55), int(ctx.height * 0.86))
        return comp, meta, lg

    def _seconds(self, s, ctx, cfg) -> float:
        comp, meta, _ = self._get(s, ctx, cfg)
        fps = ctx.fps
        travel = max(comp.height - ctx.height, 0)
        return 0.3 + (len(meta) * CASCADE_FRAMES + ROW_SLIDE_FRAMES) / fps + SCROLL_DELAY + travel / cfg.scroll_speed + cfg.hold_seconds + (ctx.height + 4) / EXIT_PX_PER_FRAME / fps

    def _get(self, s, ctx, cfg):
        key = s["abbrev"]
        if key not in self._built:
            self._built[key] = self._build(s, ctx, cfg)
        return self._built[key]

    # -- playback ---------------------------------------------------------------------

    def render(self, ctx: BoardContext, cfg: TeamSummaryConfig) -> Image.Image:
        if not self._teams or self._size != (ctx.width, ctx.height):
            self.enter(ctx, cfg)
        w, h = ctx.width, ctx.height
        out = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        if not self._teams:
            return out.convert("RGB")
        t = ctx.elapsed
        idx = 0
        while idx < len(self._timeline) - 1 and t >= self._timeline[idx]:
            t -= self._timeline[idx]
            idx += 1
        s = self._teams[idx]
        comp, meta, lg = self._get(s, ctx, cfg)
        fps = ctx.fps
        logo_in = 0.3
        cascade_end = logo_in + (len(meta) * CASCADE_FRAMES + ROW_SLIDE_FRAMES) / fps
        travel = max(comp.height - h, 0)
        scroll_start = cascade_end + SCROLL_DELAY
        scroll_end = scroll_start + travel / cfg.scroll_speed
        exit_start = scroll_end + cfg.hold_seconds
        exit_px = int((t - exit_start) * fps) * EXIT_PX_PER_FRAME if t >= exit_start else 0
        # gradient column (left), text, logo (top)
        grad = reflected_gradient(60, h)
        out.alpha_composite(grad, (-10, -exit_px)) if exit_px <= h else None
        offset = int(min(max(t - scroll_start, 0) * cfg.scroll_speed, travel))
        if t < cascade_end:
            frame_no = int((t - logo_in) * fps)
            for i, (y, animated, hh) in enumerate(meta):
                start = i * CASCADE_FRAMES
                if frame_no < start:
                    continue
                strip = comp.crop((0, y, w, y + hh))
                if animated and frame_no < start + ROW_SLIDE_FRAMES:
                    k = quintic_out((frame_no - start + 1) / ROW_SLIDE_FRAMES)
                    dy = int(hh * (1 - k))
                    strip = strip.crop((0, 0, w, hh - dy))
                    out.alpha_composite(strip, (0, y + dy))
                else:
                    out.alpha_composite(strip, (0, y))
        else:
            out.alpha_composite(comp.crop((0, offset, w, offset + h)), (0, -exit_px))
        # logo: slides in from the right over 0.3s (quintic), then loops a sheen
        lx, ly = int(w * 0.70) - lg.width // 2, (h - lg.height) // 2
        k = quintic_out(min(t / logo_in, 1.0))
        node = Sheen(Img(lg), period=cfg.sheen_seconds, band=40, strength=0.8, delay=logo_in)
        limg = render_node(node, t)
        out.paste(limg, (lx + int(lg.width * (1 - k)), ly - exit_px), limg)
        return out.convert("RGB")

    def done(self, ctx: BoardContext, cfg: TeamSummaryConfig) -> bool:
        return bool(self._timeline) and ctx.elapsed >= sum(self._timeline)
