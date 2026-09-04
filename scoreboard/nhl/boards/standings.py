"""Standings — port of the old table: RK / TEAM chip / GP / RECORD / PTS, section bars,
row cascade-in, sticky header while scrolling, hold at the bottom, then exit upward."""
from __future__ import annotations

from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext
from ...render.fx import chip
from ...render.text import text_size
from ..teams import logo, team

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
ROW_H = 7
SECTION_GAP = 5
COLS = {"RK": 0, "TEAM": 11, "GP": 30, "RECORD": 47, "PTS": 84}   # x of each header label
PTS_RIGHT = 96
CASCADE_FRAMES = 5
ROW_SLIDE_FRAMES = 4
SCROLL_DELAY = 5.0
EXIT_PX_PER_FRAME = 3


class StandingsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Standings")
    view: Literal["division", "wildcard", "league"] = "division"
    scroll_speed: float = Field(5.0, ge=1, le=40, description="Pixels per second")
    hold_seconds: float = Field(5.0, ge=0, le=20, description="Pause at the bottom before leaving")
    highlight: list[str] = Field([], description="Team abbrevs to highlight (defaults to favourites)")


class StandingsBoard(BaseBoard):
    key = "nhl.standings"
    title = "Standings"
    config_model = StandingsConfig
    requires = frozenset({"nhl.standings"})
    standings_key = "nhl.standings"
    summary_key = "nhl.team_summary"
    points_header = "PTS"
    wildcard_cutoff = 2             # playoff line drawn after this wildcard rank

    def logo_image(self, abbrev: str) -> Image.Image:
        return logo(abbrev, 128)

    def team_colors(self, abbrev: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        t = team(abbrev)
        return t.primary, t.text_on_primary

    def _record(self, r: dict[str, Any]) -> str:
        return f"{r.get('wins', 0)}-{r.get('losses', 0)}-{r.get('otl', 0)}"

    def _points(self, r: dict[str, Any]) -> str:
        return str(r.get("points", 0))

    def __init__(self) -> None:
        self._pages: list[tuple[Image.Image, list[tuple[int, bool]]]] = []   # (composite, [(row_y, animated)])
        self._header: Image.Image | None = None
        self._size = (0, 0)
        self._timeline: list[float] = []

    # -- building ----------------------------------------------------------------

    def enter(self, ctx: BoardContext, cfg: StandingsConfig) -> None:
        standings = ctx.snapshot.get(self.standings_key) or {}
        rows = standings.get("teams") or {}
        highlight = {h.upper() for h in cfg.highlight} or set(ctx.snapshot.get(self.summary_key) or {})
        self._size = (ctx.width, ctx.height)
        f6 = ctx.profile.label_font()
        self._header = self._header_row(ctx.width, f6)
        banner = self._banner(ctx)
        self._pages = [self._page(groups, rows, highlight, ctx.width, f6, banner) for groups in self._grouped(standings, cfg)]
        self._timeline = [self._page_seconds(p, ctx, cfg) for p in self._pages]

    def _grouped(self, standings: dict[str, Any], cfg: StandingsConfig) -> list[list[tuple[str, list[str], bool]]]:
        """Pages of (title, teams, cutoff_after) sections."""
        if cfg.view == "league":
            return [[("NHL", standings.get("league", []), False)]]
        if cfg.view == "wildcard":
            pages = []
            for conf, groups in (standings.get("wildcard") or {}).items():
                sections = [(f"{conf} {grp}".upper(), teams, grp == "Wildcard") for grp, teams in groups.items()]
                pages.append(sections)
            return pages or [[]]
        divs = list((standings.get("division") or {}).items())
        return [[(n.upper(), t, False) for n, t in divs[i:i + 2]] for i in range(0, len(divs), 2)] or [[]]

    def _header_row(self, width: int, f6) -> Image.Image:
        row = Image.new("RGBA", (width, ROW_H), (0, 0, 0, 255))
        for label, x in COLS.items():
            row.alpha_composite(chip(self.points_header if label == "PTS" else label, f6, WHITE, BLACK), (x, 0))
        return row

    season_key = "nhl.season"

    def _banner(self, ctx: BoardContext) -> str | None:
        season = ctx.snapshot.get(self.season_key) or {}
        sid = season.get("standings_season_id")
        if season.get("standings_final") and sid:
            return f"FINAL {str(sid)[:4]}-{str(sid)[6:]}"
        return None

    def _page(self, sections, rows, highlight, width, f6, banner: str | None = None) -> tuple[Image.Image, list[tuple[int, bool]]]:
        strips: list[tuple[Image.Image, bool]] = []
        if banner:
            strips.append((chip(banner, f6, (0, 0, 0), (255, 200, 0), pad=(1, 1, width, 1)).crop((0, 0, width, ROW_H)), False))
        for title, teams, cutoff in sections:
            strips.append((chip(title, f6, BLACK, WHITE, pad=(1, 1, width, 1)).crop((0, 0, width, ROW_H)), False))
            for rank, abbrev in enumerate(teams, 1):
                r = rows.get(abbrev) or {}
                primary, fg = self.team_colors(abbrev)
                row = Image.new("RGBA", (width, ROW_H), (0, 0, 0, 255))
                color = WHITE if abbrev in highlight else (200, 200, 200)
                self._text(row, str(rank), f6, color, COLS["RK"], 1)
                row.alpha_composite(chip(abbrev, f6, fg, primary, pad=(2, 1, 3, 1)), (COLS["TEAM"] + 1, 0))
                self._text(row, str(r.get("gp", 0)), f6, color, COLS["GP"] + 1, 1)
                self._text(row, self._record(r), f6, color, COLS["RECORD"], 1)
                pts = self._points(r)
                self._text(row, pts, f6, color, PTS_RIGHT - text_size(pts, f6)[0], 1)
                strips.append((row, True))
                if cutoff and r.get("wildcard_rank") == self.wildcard_cutoff:
                    line = Image.new("RGBA", (width, 3), (0, 0, 0, 255))
                    for x in range(2, width - 2):
                        line.putpixel((x, 1), (100, 100, 100, 200))
                    strips.append((line, False))
            strips.append((Image.new("RGBA", (width, SECTION_GAP), (0, 0, 0, 255)), False))
        total = sum(s.height for s, _ in strips)
        comp = Image.new("RGBA", (width, max(total, 1)), (0, 0, 0, 255))
        y, meta = 0, []
        for img, animated in strips:
            comp.alpha_composite(img, (0, y))
            meta.append((y, animated))
            y += img.height
        return comp, meta

    @staticmethod
    def _text(row: Image.Image, text: str, font, color, x: int, y: int) -> None:
        from PIL import ImageDraw
        ImageDraw.Draw(row).text((x, y), text, font=font, fill=color)

    def _page_seconds(self, page, ctx: BoardContext, cfg: StandingsConfig) -> float:
        comp, meta = page
        fps = ctx.fps
        cascade = (len(meta) * CASCADE_FRAMES + ROW_SLIDE_FRAMES) / fps
        travel = max(comp.height - ctx.height, 0)
        exit_secs = (comp.height + ROW_H) / EXIT_PX_PER_FRAME / fps
        return cascade + SCROLL_DELAY + travel / cfg.scroll_speed + cfg.hold_seconds + exit_secs

    # -- playback -------------------------------------------------------------------

    def render(self, ctx: BoardContext, cfg: StandingsConfig) -> Image.Image:
        if not self._pages or self._size != (ctx.width, ctx.height):
            self.enter(ctx, cfg)
        t = ctx.elapsed
        idx = 0
        while idx < len(self._timeline) - 1 and t >= self._timeline[idx]:
            t -= self._timeline[idx]
            idx += 1
        comp, meta = self._pages[idx]
        fps = ctx.fps
        frame_no = int(t * fps)
        cascade_frames = len(meta) * CASCADE_FRAMES + ROW_SLIDE_FRAMES
        travel = max(comp.height - ctx.height, 0)
        scroll_start = cascade_frames / fps + SCROLL_DELAY
        scroll_end = scroll_start + travel / cfg.scroll_speed
        exit_start = scroll_end + cfg.hold_seconds
        out = Image.new("RGB", (ctx.width, ctx.height), BLACK)
        if t < scroll_start:
            # cascade: rows appear at 5-frame intervals with a 4-frame upward wipe
            for i, (y, animated) in enumerate(meta):
                start = i * CASCADE_FRAMES
                strip = comp.crop((0, y, ctx.width, meta[i + 1][0] if i + 1 < len(meta) else comp.height))
                if frame_no < start:
                    continue
                if animated and frame_no < start + ROW_SLIDE_FRAMES:
                    k = (frame_no - start + 1) / ROW_SLIDE_FRAMES
                    k = 1 - (1 - k) ** 5
                    dy = int(strip.height * (1 - k))
                    strip = strip.crop((0, 0, ctx.width, strip.height - dy))
                    out.paste(strip, (0, y + dy))
                else:
                    out.paste(strip, (0, y))
            return out
        if t < exit_start:
            offset = int(min(max(t - scroll_start, 0) * cfg.scroll_speed, travel))
            out.paste(comp.crop((0, offset, ctx.width, offset + ctx.height)), (0, 0))
            if offset > ROW_H and self._header is not None:
                out.paste(self._header, (0, 0))
            return out
        exit_px = int((t - exit_start) * fps) * EXIT_PX_PER_FRAME
        out.paste(comp.crop((0, travel, ctx.width, travel + ctx.height)), (0, -exit_px))
        if travel > ROW_H and self._header is not None:
            out.paste(self._header, (0, -exit_px))
        return out

    def done(self, ctx: BoardContext, cfg: StandingsConfig) -> bool:
        return bool(self._timeline) and ctx.elapsed >= sum(self._timeline)

    def auto_seconds(self, ctx: BoardContext, cfg: StandingsConfig) -> float | None:
        return sum(self._timeline) if self._timeline else None      # known once the board has been built
