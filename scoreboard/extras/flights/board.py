"""Flight boards: 'nearby' cycles through aircraft in the old Flight-Wall card layout;
'overhead' is an event board that interrupts when one passes close overhead."""
from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext, EventBoard, SequenceMixin
from ...data import Event
from ...render import Absolute, Img, Sequence, Slide, Text, load_font, render_tree
from ...render.anim import quintic_out
from ...render.text import text_size

TEXT = (255, 255, 255)
LABEL = (130, 140, 155)
DIST = (80, 200, 255)
UNKNOWN = (110, 120, 135)
TILE_AIRLINE = (25, 60, 140)
TILE_PRIVATE = (52, 58, 70)
ALERT = (255, 160, 40)


class NearbyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Flights nearby")
    seconds_per_aircraft: float = Field(6.0, ge=2, le=30)


class OverheadConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Flight overhead alert")
    enabled: bool = True
    duration: float = Field(8.0, ge=2, le=30)


def _fmt_alt(ac: dict[str, Any], metric: bool) -> str:
    if ac.get("on_ground"):
        return "GND"
    if metric:
        v = ac.get("altitude_m")
        return f"{v}m" if v is not None else ""
    v = ac.get("altitude_ft")
    return "" if v is None else (f"{v / 1000:.1f}kft" if v >= 1000 else f"{v}ft")


def _fmt_speed(ac: dict[str, Any], metric: bool) -> str:
    v = ac.get("speed_kmh" if metric else "speed_mph")
    return "" if v is None else f"{v}{'kmh' if metric else 'mph'}"


def _fmt_dist(ac: dict[str, Any], metric: bool) -> str:
    v = ac.get("distance_km" if metric else "distance_mi")
    return "" if v is None else f"{v:g}{'km' if metric else 'mi'} {ac.get('bearing_compass', '')}".strip()


def _monogram_tile(ac: dict[str, Any], side: int) -> Image.Image:
    """Airline-style square: 2-3 letter code on a coloured tile (we ship no airline logos)."""
    code = (ac.get("ident") or ac.get("callsign") or "??")[:3]
    airline = bool(ac.get("airline"))
    tile = Image.new("RGBA", (side, side), (*(TILE_AIRLINE if airline else TILE_PRIVATE), 255))
    d = ImageDraw.Draw(tile)
    font = load_font("block", max(6, side // 3))
    w, h = text_size(code, font)
    d.text(((side - w) // 2, (side - h) // 2), code, font=font, fill=TEXT)
    d.rectangle((0, 0, side - 1, side - 1), outline=(255, 255, 255, 60))
    return tile


def card(ac: dict[str, Any], width: int, height: int, metric: bool, header: str | None = None) -> list:
    """Flight-Wall layout as Absolute items: tile left, ident/route/type beside, telemetry rows under."""
    f6 = load_font("pl", 6)
    margin, gap = 2, 2
    y0 = 0
    items = []
    if header:
        items.append((Img(_bar(header, width)), 0, 0, width, 7))
        y0 = 8
    avail_h = height - y0
    side = max(16, min(28, avail_h - 14))
    items.append((Slide(Img(_monogram_tile(ac, side)), 0.4, "left", easing=quintic_out), margin, y0 + 1, side, side))
    tx = margin + side + 4
    tw = width - tx - margin
    line1 = ac.get("airline") or ac.get("ident") or "?"
    line2 = ac.get("route") or ("Route unknown" if ac.get("callsign") else ac.get("registration") or "")
    line3 = ac.get("type_name") or ac.get("type") or ac.get("registration") or ""
    rows = [(line1, TEXT), (line2, TEXT if ac.get("route") else UNKNOWN), (line3, TEXT)]
    y = y0 + 1
    for i, (txt, color) in enumerate(rows):
        if txt:
            items.append((Slide(Text(txt[:22], f6, color), 0.3, "up", delay=0.05 * i, easing=quintic_out, h_align="start"), tx, y, tw, 6))
        y += 6 + gap
    dist = _fmt_dist(ac, metric)
    if dist:
        dw = text_size(dist, f6)[0]
        items.append((Text(dist, f6, DIST), width - margin - dw, y0 + 1 + 2 * (6 + gap), dw, 6))
    ty = y0 + side + 3
    tele = [("Alt", _fmt_alt(ac, metric)), ("Spd", _fmt_speed(ac, metric)),
            ("Hdg", f"{ac['heading']:03d}" if ac.get("heading") is not None else "")]
    x = margin
    for label, value in tele:
        if not value or ty + 6 > height:
            continue
        lw, vw = text_size(f"{label}:", f6)[0], text_size(value, f6)[0]
        if x + lw + 2 + vw > width - margin:
            break
        items.append((Text(f"{label}:", f6, LABEL), x, ty, lw, 6))
        x += lw + 2
        items.append((Text(value, f6, TEXT), x, ty, vw, 6))
        x += vw + 6
    return items


def compact_card(ac: dict[str, Any], width: int, height: int, metric: bool) -> list:
    """Two-line card for 64x32: ident + distance, then route or alt/speed."""
    f6 = load_font("pl", 6)
    ident = ac.get("ident") or ac.get("callsign") or "?"
    dist = _fmt_dist(ac, metric)
    line2 = ac.get("route") or " ".join(x for x in (_fmt_alt(ac, metric), _fmt_speed(ac, metric)) if x)
    y = max(0, (height - 14) // 2)
    items = [(Slide(Text(ident[:10], f6, TEXT), 0.3, "up", easing=quintic_out, h_align="start"), 1, y, width - 2, 6)]
    if dist:
        dw = text_size(dist, f6)[0]
        items.append((Text(dist, f6, DIST), width - 1 - dw, y, dw, 6))
    if line2:
        items.append((Slide(Text(line2[:16], f6, TEXT), 0.3, "up", delay=0.05, easing=quintic_out, h_align="start"), 1, y + 8, width - 2, 6))
    return items


def _bar(text: str, width: int) -> Image.Image:
    from ...render.fx import chip
    return chip(text, load_font("pl", 6), (0, 0, 0), ALERT, pad=(1, 1, width, 1)).crop((0, 0, width, 7))


class NearbyBoard(BaseBoard):
    key = "flights.nearby"
    title = "Flights nearby"
    config_model = NearbyConfig
    requires = frozenset({"flights.nearby"})

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []

    def enter(self, ctx: BoardContext, cfg: NearbyConfig) -> None:
        self._items = list(ctx.snapshot.get("flights.nearby") or [])

    def done(self, ctx: BoardContext, cfg: NearbyConfig) -> bool:
        return ctx.elapsed >= cfg.seconds_per_aircraft * max(len(self._items), 1)

    def render(self, ctx: BoardContext, cfg: NearbyConfig) -> Image.Image:
        if not self._items:
            self.enter(ctx, cfg)
        w, h = ctx.width, ctx.height
        if not self._items:
            return render_tree(Text("NO AIRCRAFT NEARBY", load_font("pl", 6), LABEL), w, h)
        idx = min(int(ctx.elapsed // cfg.seconds_per_aircraft), len(self._items) - 1)
        local = ctx.elapsed - idx * cfg.seconds_per_aircraft
        metric = _metric(ctx)
        layout = compact_card if h <= 32 else card
        return render_tree(Absolute(layout(self._items[idx], w, h, metric)), w, h, t=local)


class OverheadBoard(SequenceMixin, EventBoard):
    key = "flights.overhead"
    title = "Flight overhead alert"
    config_model = OverheadConfig
    event_kinds = frozenset({"flights.overhead"})

    def matches(self, event: Event, cfg: OverheadConfig) -> bool:
        return cfg.enabled and event.kind in self.event_kinds

    def build(self, ctx: BoardContext, cfg: OverheadConfig) -> Sequence:
        ac = (ctx.event.payload.get("aircraft") if ctx.event else None) or {}
        frames = [render_tree(Absolute(card(ac, ctx.width, ctx.height, _metric(ctx), header="OVERHEAD")), ctx.width, ctx.height, t=i / ctx.fps)
                  for i in range(int(cfg.duration * ctx.fps))]
        return Sequence(ctx.fps).frames(frames).build(frames[0] if frames else Image.new("RGB", (ctx.width, ctx.height)))


def _metric(ctx: BoardContext) -> bool:
    items = ctx.snapshot.get("flights.nearby") or []
    return bool(items and items[0].get("metric"))
