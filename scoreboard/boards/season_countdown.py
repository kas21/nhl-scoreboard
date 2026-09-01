"""Season countdown: days until the next milestone (your team's opener, or the season start)."""
from __future__ import annotations

from datetime import date
from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ..render import Absolute, Img, Sheen, Slide, Text, VBox, load_font, render_tree
from ..render.anim import quintic_out
from ..render.fx import fit_logo
from .base import BaseBoard, BoardContext

NUMBER = (80, 200, 255)
LABEL = (160, 170, 180)
NAME = (255, 255, 255)
SUB = (255, 220, 100)
WORDS = {"nhl": ("PUCK DROP", "NHL"), "nfl": ("KICKOFF", "NFL")}


class CountdownConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Season countdown")
    sport: str = Field("auto", description="'auto' follows sports.priority; or nhl / nfl")


def milestone(season: dict[str, Any], today: date) -> dict[str, Any] | None:
    """What to count down to, from a sport's season record. None during the season."""
    phase = season.get("phase")
    if phase in ("regular", "playoffs"):
        return None
    first = season.get("first_game")
    if first and first.get("date"):
        try:
            days = (date.fromisoformat(first["date"]) - today).days
        except ValueError:
            days = None
        if days is not None and days >= 0:
            vs = "VS" if first.get("home") else "AT"
            return {"days": days, "label": f"OPENER {vs} {first.get('opponent', '')}".strip(), "date": first["date"], "team": season.get("favorite")}
    if phase == "offseason" and season.get("days_to_preseason") is not None and season["days_to_preseason"] >= 0:
        return {"days": season["days_to_preseason"], "label": "PRESEASON", "date": season.get("preseason_start")}
    if season.get("days_to_regular") is not None and season["days_to_regular"] >= 0:
        return {"days": season["days_to_regular"], "label": WORDS.get(season.get("sport", ""), ("SEASON", ""))[0], "date": season.get("regular_start")}
    if season.get("days_to_next") is not None:
        return {"days": season["days_to_next"], "label": "NEXT GAME", "date": season.get("next_game_date")}
    return None


class SeasonCountdownBoard(BaseBoard):
    key = "season.countdown"
    title = "Season countdown"
    config_model = CountdownConfig

    def _pick(self, ctx: BoardContext, cfg: CountdownConfig) -> tuple[str, dict[str, Any]] | None:
        seasons = {k[: -len(".season")]: v for k, v in ctx.snapshot.data.items() if k.endswith(".season") and isinstance(v, dict)}
        if cfg.sport != "auto" and cfg.sport in seasons:
            order = [cfg.sport]
        else:
            order = [s for s in ("nhl", "nfl") if s in seasons] + sorted(s for s in seasons if s not in ("nhl", "nfl"))
        for s in order:
            m = milestone(seasons[s], ctx.now.date())
            if m:
                return s, m
        return None

    def done(self, ctx: BoardContext, cfg: CountdownConfig) -> bool:
        return self._pick(ctx, cfg) is None      # nothing to count down to: skip immediately

    def render(self, ctx: BoardContext, cfg: CountdownConfig) -> Image.Image:
        w, h = ctx.width, ctx.height
        picked = self._pick(ctx, cfg)
        if not picked:
            return Image.new("RGB", (w, h))
        sport, m = picked
        big, small = load_font("pl", 12), ctx.profile.label_font()
        items = []
        text_x, text_w = 0, w
        logo_img = self._logo(sport, ctx)
        if logo_img is not None and w >= 96:
            lg = fit_logo(logo_img, h - 4, h - 4)
            items.append((Slide(Img(lg), 0.5, "left", easing=quintic_out), 2, 2, lg.width, lg.height))
            text_x, text_w = lg.width + 6, w - lg.width - 8
        days = m["days"]
        rows = [Sheen(Text(str(days), big, NUMBER), period=3.0, band=10, strength=0.6, once=True, delay=0.6),
                Text("DAY TIL" if days == 1 else "DAYS TIL" if days else "TODAY", small, LABEL),
                Text(m["label"][:18], small, NAME)]
        if m.get("date"):
            try:
                rows.append(Text(date.fromisoformat(m["date"]).strftime("%b %-d").upper(), small, SUB))
            except ValueError:
                pass
        items.append((Slide(VBox(rows, spacing=1), 0.4, "up", delay=0.1, easing=quintic_out), text_x, 0, text_w, h))
        return render_tree(Absolute(items), w, h, t=ctx.elapsed)

    @staticmethod
    def _logo(sport: str, ctx: BoardContext) -> Image.Image | None:
        summaries = ctx.snapshot.get(f"{sport}.team_summary") or {}
        fav = next(iter(summaries), None)
        try:
            if sport == "nhl":
                from ..nhl.teams import logo
                return logo(fav, 128) if fav else None
            if sport == "nfl":
                from ..nfl.teams import logo
                return logo(fav or "NFL", 128)
        except Exception:
            return None
        return None
