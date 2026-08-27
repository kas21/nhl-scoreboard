"""Flight boards: 'nearby' cycles through aircraft in the old Flight-Wall card layout;
'overhead' is an event board that interrupts when one passes close overhead."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext, EventBoard, SequenceMixin
from ...data import Event
from ...imagecache import load as cached_image
from ...render import Absolute, Img, Sequence, Slide, Text, load_font, render_tree
from ...render.anim import quintic_out
from ...render.fx import fit_logo
from ...render.text import text_size

TEXT = (255, 255, 255)
LABEL = (130, 140, 155)
DIST = (80, 200, 255)
UNKNOWN = (110, 120, 135)
TILE_AIRLINE = (25, 60, 140)
TILE_PRIVATE = (52, 58, 70)
ALERT = (255, 160, 40)

LOGO_MAX, LOGO_MIN = 40, 16      # the old Flight-Wall card's logo block
LINE_H, MARGIN, GAP, BLOCK_GAP = 6, 2, 3, 4


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


def _fmt_vs(ac: dict[str, Any], metric: bool) -> str:
    v = ac.get("vertical_rate_fpm")
    if not v:                       # missing or level flight: nothing worth a slot
        return ""
    return f"{v * 0.00508:+.1f}m/s" if metric else f"{v:+d}fpm"


def _fmt_dist(ac: dict[str, Any], metric: bool) -> str:
    v = ac.get("distance_km" if metric else "distance_mi")
    return "" if v is None else f"{v:g}{'km' if metric else 'mi'} {ac.get('bearing_compass', '')}".strip()


def _monogram_tile(ac: dict[str, Any], side: int) -> Image.Image:
    """Fallback when no logo resolved: 2-3 letter operator code on a coloured tile."""
    code = (ac.get("airline_iata") or ac.get("airline_icao") or ac.get("callsign") or ac.get("ident") or "??")[:3]
    airline = bool(ac.get("airline"))
    tile = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    d.rounded_rectangle((0, 0, side - 1, side - 1), radius=3, fill=(*(TILE_AIRLINE if airline else TILE_PRIVATE), 255))
    font = load_font("block", max(6, side // 3))
    w, h = text_size(code, font)
    if w > side - 2:
        font = load_font("pl", 6)
        w, h = text_size(code, font)
    d.text(((side - w) // 2, (side - h) // 2), code, font=font, fill=TEXT)
    return tile


def _logo_tile(ac: dict[str, Any], side: int) -> Image.Image:
    """The airline's logo when the source fetched one, else a monogram tile."""
    logo = cached_image(Path(ac["logo"]), side) if ac.get("logo") else None
    return _monogram_tile(ac, side) if logo is None else fit_logo(logo, side, side)


def _clip(text: str, font: Any, max_width: int) -> str:
    """Longest prefix that fits; bitmap fonts have no room to spare for an ellipsis."""
    while text and text_size(text, font)[0] > max_width:
        text = text[:-1]
    return text


def _telemetry_rows(ac: dict[str, Any], metric: bool, font: Any, width: int) -> list[list[tuple[str, str, int, int]]]:
    """Label/value pairs flowed into rows that fit ``width`` (Alt/Spd, then Hdg/VS as the old client did)."""
    rows: list[list[tuple[str, str, int, int]]] = []
    row: list[tuple[str, str, int, int]] = []
    x = MARGIN
    for label, value in (("Alt", _fmt_alt(ac, metric)), ("Spd", _fmt_speed(ac, metric)),
                         ("Hdg", f"{ac['heading']:03d}" if ac.get("heading") is not None else ""),
                         ("VS", _fmt_vs(ac, metric))):
        if not value:
            continue
        lw, vw = text_size(f"{label}:", font)[0], text_size(value, font)[0]
        if row and x + lw + 2 + vw > width - MARGIN:
            rows.append(row)
            row, x = [], MARGIN
        row.append((label, value, lw, vw))
        x += lw + 2 + vw + 6
    if row:
        rows.append(row)
    return rows


def _info_rows(ac: dict[str, Any]) -> list[tuple[str, tuple[int, int, int]]]:
    route = ac.get("route")
    rows = [
        (ac.get("airline") or ac.get("ident") or "?", TEXT),
        (route or ("Route unknown" if ac.get("callsign") else ac.get("registration") or ""), TEXT if route else UNKNOWN),
        (ac.get("type_name") or ac.get("type") or ac.get("registration") or "", TEXT),
    ]
    return [(txt, color) for txt, color in rows if txt]


def card(ac: dict[str, Any], width: int, height: int, metric: bool, header: str | None = None) -> list:
    """Flight-Wall layout as Absolute items: logo left, airline/route/type beside, telemetry under."""
    f6 = load_font("pl", 6)
    items = []
    y0 = 0
    if header:
        items.append((Img(_bar(header, width)), 0, 0, width, 7))
        y0 = 8
    avail_h = height - y0

    rows = _info_rows(ac)
    top_h = len(rows) * LINE_H + GAP * (len(rows) - 1)
    tele = _telemetry_rows(ac, metric, f6, width)
    tele = tele[: max(1, (avail_h - LOGO_MIN - BLOCK_GAP) // (LINE_H + GAP))]
    tele_h = len(tele) * LINE_H + GAP * (len(tele) - 1)
    # Logo block, square, as large as the space beside the text and above the telemetry allows
    side = max(LOGO_MIN, min(LOGO_MAX, avail_h - tele_h - BLOCK_GAP - 2, width // 3))
    top_block_h = max(side, top_h)
    y = y0 + max(0, (avail_h - top_block_h - BLOCK_GAP - tele_h) // 2)
    items.append((Slide(Img(_logo_tile(ac, side)), 0.4, "left", easing=quintic_out),
                  MARGIN, y + (top_block_h - side) // 2, side, side))

    tx = MARGIN + side + 4
    tw = width - tx - MARGIN
    dist = _fmt_dist(ac, metric)
    dw = text_size(dist, f6)[0] if dist else 0
    ty = y + max(0, (top_block_h - top_h) // 2)
    for i, (txt, color) in enumerate(rows):
        last = i == len(rows) - 1
        room = tw - dw - 4 if (dist and last) else tw     # the type line shares its row with the distance
        items.append((Slide(Text(_clip(txt, f6, room), f6, color), 0.3, "up", delay=0.05 * i,
                            easing=quintic_out, h_align="start"), tx, ty, room, LINE_H))
        if dist and last:
            items.append((Text(dist, f6, DIST), width - MARGIN - dw, ty, dw, LINE_H))
        ty += LINE_H + GAP

    ty = y + top_block_h + BLOCK_GAP
    for row in tele:
        x = MARGIN
        for label, value, lw, vw in row:
            items.append((Text(f"{label}:", f6, LABEL), x, ty, lw, LINE_H))
            items.append((Text(value, f6, TEXT), x + lw + 2, ty, vw, LINE_H))
            x += lw + 2 + vw + 6
        ty += LINE_H + GAP
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
