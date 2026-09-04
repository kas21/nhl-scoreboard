"""MLB game board: the NHL XL layout with baseball specifics — inning + half arrow, bases
diamond, count and outs, pitcher / batter strip, due-up during inning breaks, probable
pitchers before the game, hits and pitching decisions after it.

The bases / outs / count cluster is MLB-LED-Scoreboard's (the sister project), squeezed into
the 60px centre column between the two logos.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from ...nhl.boards.game import BLACK, HYPHEN, RED, SCORE_AWAY_X, SCORE_HOME_X, SCORE_Y
from ...nhl.boards.game import GameBoard as NhlGameBoard
from ...render import Anchor, Box, HBox, Img, Marquee, Slide, Spacer, Text
from ...render.anim import cubic_out, quartic_out
from ...render.fx import Chip
from ..normalize import last_name
from ..teams import logo, team

WHITE = (255, 255, 255)
GREY = (190, 190, 190)
DIM = (110, 110, 110)
AMBER = (255, 200, 0)
GREEN = (0, 255, 0)
STATS_Y = 40                    # top of the count / bases / outs cluster
STRIP_Y = 57                    # pitcher / batter strip (below the logos, full width)


class MlbGameConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="MLB game board")
    show_records: bool = True
    show_bases: bool = Field(True, description="Bases diamond, count and outs while a half-inning is in play")
    show_pitcher_batter: bool = Field(True, description="Pitcher / batter (and due-up between innings) along the bottom")
    show_last_pitch: bool = Field(True, description="Speed and type of the last pitch next to the pitcher")
    show_hits: bool = Field(True, description="Hits and pitching decisions on the final")
    time_24h: bool = False
    show_sog: bool = False        # unused for baseball; kept so the shared layout code can read it


@lru_cache(maxsize=16)
def bases_image(runners: tuple[bool, bool, bool], on: tuple[int, int, int] = AMBER, off: tuple[int, int, int] = DIM) -> Image.Image:
    """Three diamonds (3B left, 2B top, 1B right), filled where a runner stands. 17x10."""
    img = Image.new("RGBA", (17, 10), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for (x, y), occupied in zip(((12, 5), (6, 0), (0, 5)), runners):            # 1B, 2B, 3B
        pts = [(x + 2, y), (x + 4, y + 2), (x + 2, y + 4), (x, y + 2)]
        if occupied:
            d.polygon(pts, fill=(*on, 255), outline=(*on, 255))
        else:
            d.polygon(pts, outline=(*off, 255))
    return img


@lru_cache(maxsize=4)
def arrow_image(up: bool, color: tuple[int, int, int] = WHITE) -> Image.Image:
    """5x3 half-inning arrow: up = top (visitors bat), down = bottom."""
    img = Image.new("RGBA", (5, 3), (0, 0, 0, 0))
    px = img.load()
    rows = ((2, 2), (1, 3), (0, 4)) if up else ((0, 4), (1, 3), (2, 2))
    for y, (x0, x1) in enumerate(rows):
        for x in range(x0, x1 + 1):
            px[x, y] = (*color, 255)
    return img


def outs_node(outs: int):
    return HBox([Box(3, 3, (255, 255, 255, 255) if i < outs else (70, 70, 70, 255)) for i in range(3)], spacing=2)


class MlbGameBoard(NhlGameBoard):
    key = "mlb.game"
    title = "MLB game"
    config_model = MlbGameConfig
    sport = "mlb"

    def logo_image(self, abbrev: str, g: dict[str, Any]) -> Image.Image:
        return logo(abbrev, 128)

    def side_colors(self, g: dict[str, Any], side: str):
        t = team(g[side]["abbrev"])
        return t.primary, t.text_on_primary

    # -- shared bits ---------------------------------------------------------

    def _pre_chip(self, g: dict[str, Any], font) -> list:
        """SPRING / ALL-STAR / WILD CARD / ALDS ... / GM2 tag where the NHL board puts PRE."""
        label = g.get("series") or ""
        if g.get("game_type") == "R" and not label:
            return []
        if not label:
            return super()._pre_chip(g, font)
        return [(Chip(label, font, BLACK, AMBER), 34, 5, 60, 7)]

    def _bottom_strip(self, candidates: list[list], width: int) -> list:
        """Full-width text strip under the logos. ``candidates`` are part lists, fullest first: the
        first that fits is spread left/right; if none fits, the fullest scrolls."""
        candidates = [c for c in candidates if c]
        if not candidates:
            return []
        for parts in candidates:
            row = HBox(parts, spacing=2)
            if row.measure()[0] <= width - 4:
                half = len(parts) // 2
                spread = HBox([*parts[:half], Spacer(), *parts[half:]], spacing=2) if len(parts) >= 4 else row
                return [(Slide(spread, 0.5, "up", easing=cubic_out), 2, STRIP_Y, width - 4, 6)]
        node = Marquee(HBox(candidates[0], spacing=2), width=width - 4, speed=16.0, pause=2.0)
        return [(node, 2, STRIP_Y, width - 4, 6)]

    # -- pregame -------------------------------------------------------------

    def _pregame(self, g, ctx, cfg) -> list:
        items = super()._pregame(g, ctx, cfg)
        f6 = ctx.profile.label_font()
        for side, y, align in (("away", 45, "start"), ("home", 52, "end")):
            name = last_name(g[side].get("probable_pitcher") or "").upper()
            if name:
                node = Marquee(Text(name, f6, GREY), width=58, h_align=align)
                items.append((Slide(node, 0.5, "up", delay=0.2, easing=cubic_out, h_align=align), 35, y, 58, 6))
        if g.get("start_tbd"):
            items = [it for it in items if not (isinstance(it[0], Text) and it[1:] == (39, 22, 50, 5))]
            items.append((Text("TBD", f6, WHITE), 39, 22, 50, 5))
        return items

    # -- live ------------------------------------------------------------------

    def _live(self, g, ctx, cfg) -> list:
        f6 = ctx.profile.label_font()
        t = ctx.elapsed
        sit = g.get("situation") or {}
        half, ordinal = sit.get("half", "top"), (sit.get("inning_ordinal") or "").upper()
        if half in ("top", "bottom"):
            strip = HBox([Chip(ordinal, f6, BLACK, WHITE), Img(arrow_image(half == "top"))], spacing=2)
        else:
            strip = HBox([Chip("MID" if half == "middle" else "END", f6, BLACK, WHITE), Text(ordinal, f6, WHITE)], spacing=1)
        if sit.get("delay"):
            strip = HBox([Chip(ordinal, f6, BLACK, WHITE), Chip("DELAY", f6, BLACK, AMBER)], spacing=1)
        items = self._pre_chip(g, f6) + [
            (strip, 34, 13, 60, 8),                 # chip is 8 tall: ink lands on the NHL strip's rows
            (self._score(g["away"]["score"], "end"), SCORE_AWAY_X - 10, SCORE_Y, 18, 12),
            (Box(fill=(255, 255, 255, 255)), *HYPHEN),
            (self._score(g["home"]["score"], "start"), SCORE_HOME_X, SCORE_Y, 18, 12),
        ]
        items += self._live_stats_row(g, cfg, f6)
        items += self._indicators(g, t, f6)
        items += self._bottom_strip(self._strip_parts(g, cfg, f6), ctx.width)
        return items

    def _live_stats_row(self, g: dict[str, Any], cfg, f6) -> list:
        """Count, bases diamond and outs where the NHL board shows SOG (only while a half is in play)."""
        sit = g.get("situation") or {}
        if not getattr(cfg, "show_bases", True) or sit.get("half") not in ("top", "bottom"):
            return []
        runners = tuple(bool(r) for r in (sit.get("runners") or [False, False, False])[:3])
        return [
            (Anchor(Text(f"{sit.get('balls', 0)}-{sit.get('strikes', 0)}", f6, WHITE), h="end"), 35, STATS_Y + 3, 16, 6),
            (Img(bases_image(runners)), 55, STATS_Y, 17, 10),
            (Anchor(outs_node(int(sit.get("outs") or 0)), h="start"), 78, STATS_Y + 4, 13, 3),
        ]

    def _strip_parts(self, g: dict[str, Any], cfg, f6) -> list[list]:
        """Bottom-strip candidates, fullest first (pitch spelled out, pitch code, no pitch)."""
        sit = g.get("situation") or {}
        if not getattr(cfg, "show_pitcher_batter", True):
            return []
        if sit.get("delay"):
            return [[Chip(sit["delay"], f6, BLACK, AMBER)]]
        if sit.get("half") in ("middle", "end"):
            names = [last_name(n).upper() for n in (sit.get("batter"), sit.get("on_deck"), sit.get("in_hole")) if n]
            return [[Chip("DUE UP", f6, BLACK, WHITE), Text(" ".join(names), f6, GREY)]] if names else []
        pitcher: list = []
        if sit.get("pitcher"):
            label = last_name(sit["pitcher"]).upper()
            if sit.get("pitch_count") is not None:
                label += f" {sit['pitch_count']}P"
            pitcher = [Chip("P", f6, BLACK, WHITE), Text(label, f6, GREY)]
        batter = [Chip("AB", f6, BLACK, WHITE), Text(last_name(sit["batter"]).upper(), f6, GREY)] if sit.get("batter") else []
        pitch = sit.get("pitch") or {}
        if not (getattr(cfg, "show_last_pitch", True) and pitch.get("speed") and pitcher):
            return [pitcher + batter]
        spelled = [Text(f"{pitch['speed']} {pitch.get('label') or pitch.get('code', '')}".strip(), f6, DIM)]
        coded = [Text(f"{pitch['speed']} {pitch.get('code', '')}".strip(), f6, DIM)]
        return [pitcher + spelled + batter, pitcher + coded + batter, pitcher + batter]

    def _indicators(self, g: dict[str, Any], t: float, f6) -> list:
        """AT BAT / last-play chip in the top corner of the batting side; no-hitter flag top centre."""
        items: list = []
        sit = g.get("situation") or {}
        batting = sit.get("batting") if sit.get("half") in ("top", "bottom") else None
        last = sit.get("last_play") or {}
        for side, x, align in (("away", 0, "start"), ("home", 82, "end")):
            label = "AT BAT"
            if batting == side and last.get("complete") and last.get("batting", side) == side and last.get("label"):
                label = last["label"]
            key = f"bat:{side}:{label}"
            self._seen = {k: v for k, v in self._seen.items() if not k.startswith(f"bat:{side}:") or k == key}
            since = self._since(key, batting == side, t)
            if since is not None:
                node = self._chip(label, g, side, f6)
                items.append((Slide(node, 0.6, "up", delay=since, easing=quartic_out, h_align=align), x, 0, 45, 7))
        flag = "PERFECT" if sit.get("perfect_game") else "NO-NO" if sit.get("no_hitter") else ""
        since = self._since(f"flag:{flag}", bool(flag) and int(sit.get("inning") or 0) >= 6, t)
        if since is not None:
            items.append((Slide(Chip(flag, f6, BLACK, AMBER), 0.4, "down", delay=since, easing=quartic_out), 46, 0, 36, 7))
        brk = self._since("break", sit.get("half") in ("middle", "end"), t)
        if brk is not None:
            items.append((Slide(Box(36, 3, (*GREEN, 255)), 0.3, "down", delay=brk, easing=quartic_out), 46, 0, 36, 3))
        return items

    # -- final -----------------------------------------------------------------

    def _final(self, g, ctx, cfg) -> list:
        if g.get("outcome") in ("PPD", "CANCELLED", "SUSPENDED"):
            f6 = ctx.profile.label_font()
            sit = g.get("situation") or {}
            reason = (sit.get("delay") or "").replace(" DELAY", "") or g["outcome"]
            return self._teams_info(g, cfg, f6) + [
                (Chip(g["outcome"], f6, WHITE, RED), 34, 14, 60, 7),
                (Text(reason.upper()[:14], f6, GREY), 34, 30, 60, 6),
            ]
        return super()._final(g, ctx, cfg)

    def _final_stats_row(self, g: dict[str, Any], cfg, f6) -> list:
        if not getattr(cfg, "show_hits", True):
            return []
        items = [
            (Anchor(Text(str(g["away"].get("hits", 0)), f6, WHITE), h="end"), 38, 43, 16, 6),
            (Chip("HITS", f6, BLACK, WHITE), 56, 42, 16, 8),
            (Anchor(Text(str(g["home"].get("hits", 0)), f6, WHITE), h="start"), 74, 43, 16, 6),
        ]
        dec = g.get("decisions") or {}
        parts = [f"{k} {last_name(dec[r].split(' (')[0]).upper()}" for k, r in (("W", "winner"), ("L", "loser"), ("S", "save")) if dec.get(r)]
        if parts:
            items.append((Marquee(Text("  ".join(parts), f6, GREY), width=58, speed=14.0, pause=2.0), 35, 51, 58, 6))
        return items
