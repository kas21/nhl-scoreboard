"""Weather alert boards.

A colour bar names the level (red warning, orange watch, yellow advisory), the hazard sits
under it in the biggest pixel face that fits, then when it ends and where; 128x64 pages
the agency's description underneath. ``weather.alerts`` cycles through everything in
force; ``weather.alert`` flashes the level colour and holds one card when a new alert
lands.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ....boards.base import BaseBoard, BoardContext, EventBoard, SequenceMixin
from ....data import Event
from ....render import (
    Absolute,
    Anchor,
    Cycle,
    Img,
    Marquee,
    Pulse,
    Sequence,
    Text,
    VBox,
    render_tree,
)
from ....render.fx import chip
from ....render.text import fit_font, text_size
from .model import LEVEL_RANK, in_force, parse_when
from .source import ALERTS_KEY, EVENT_KIND

LEVEL_COLORS: dict[str, tuple[int, int, int]] = {
    "warning": (255, 40, 40), "watch": (255, 150, 0), "advisory": (255, 215, 0),
    "statement": (70, 150, 255), "other": (170, 170, 170),
}
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAY = (170, 170, 170)
LIGHT = (215, 215, 215)
DIVIDER = (60, 60, 60)
BAR_H, LINE_H, LINE_GAP = 7, 6, 1
LINES_PER_PAGE, MAX_PAGES, PAGE_SWAP = 3, 3, 0.4
MARQUEE_SPEED = 25.0
PULSE_PERIOD, PULSE_LOW = 1.2, 0.45     # a warning's bar breathes; everything else holds still
FLASH_SECONDS = 0.8
NAME_FONT = "pixelbold"
COMPACT_BELOW = 48                      # panels shorter than this get the four-row layout


class AlertsBoardConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Weather alerts board")
    seconds_per_alert: float = Field(10.0, ge=3, le=60)
    page_seconds: float = Field(4.0, ge=2, le=15, description="How long each page of the description holds (128x64)")


class AlertBoardConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Weather alert interrupt")
    enabled: bool = True
    duration: float = Field(10.0, ge=3, le=30, description="Seconds the card holds after the flash")
    min_level: Literal["warning", "watch", "advisory"] = Field("warning", description="Interrupt the rotation for alerts at least this serious")
    interrupt_live_game: bool = Field(True, description="Interrupt a live game too (off: the alert still shows on the alerts board between periods)")


def until_text(expires: str | None, now: datetime) -> str:
    """'UNTIL 4:45 PM' in the panel's zone, with the weekday when it is not today."""
    when = parse_when(expires)
    if when is None:
        return ""
    local = when.astimezone(now.tzinfo)
    clock = f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"
    day = "" if local.date() == now.date() else f"{local.strftime('%a').upper()} "
    return f"UNTIL {day}{clock}"


def level_color(alert: dict[str, Any]) -> tuple[int, int, int]:
    return LEVEL_COLORS.get(alert.get("level") or "other", LEVEL_COLORS["other"])


def wrap(text: str, font: Any, max_width: int) -> list[str]:
    """Greedy word wrap measured with the font that draws it."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and text_size(candidate, font)[0] > max_width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def _bar(alert: dict[str, Any], width: int, f6: Any, counter: str) -> Image.Image:
    color = level_color(alert)
    img = chip((alert.get("level") or "alert").upper(), f6, BLACK, color, pad=(2, 1, width, 1)).crop((0, 0, width, BAR_H))
    if counter:
        tag = chip(counter, f6, BLACK, color, pad=(1, 1, 1, 1))
        img.alpha_composite(tag, (width - tag.width - 1, 0))
    return img


def _bar_node(alert: dict[str, Any], width: int, f6: Any, counter: str):
    node = Img(_bar(alert, width, f6, counter))
    return Pulse(node, period=PULSE_PERIOD, low=PULSE_LOW) if alert.get("level") == "warning" else node


def _name(alert: dict[str, Any]) -> str:
    return (alert.get("name") or alert.get("event") or "WEATHER ALERT").upper()


def full_card(alert: dict[str, Any], w: int, h: int, f6: Any, now: datetime, counter: str, page_seconds: float) -> Absolute:
    color = level_color(alert)
    name = _name(alert)
    items: list[tuple[Any, int, int, int, int]] = [
        (_bar_node(alert, w, f6, counter), 0, 0, w, BAR_H),
        (Marquee(Text(name, fit_font(name, NAME_FONT, w - 2, 13, 8), WHITE), w - 2, speed=MARQUEE_SPEED, h_align="start"), 1, 9, w - 2, 13),
        (Marquee(Text(alert.get("area") or "", f6, GRAY), w - 2, speed=MARQUEE_SPEED, h_align="start"), 1, 31, w - 2, LINE_H),
        (Img(Image.new("RGBA", (w, 1), (*DIVIDER, 255))), 0, 38, w, 1),
    ]
    until = until_text(alert.get("expires"), now)
    if until:
        items.append((Anchor(Text(until, f6, color), h="start"), 1, 24, w - 2, LINE_H))
    lines = wrap(alert.get("summary") or alert.get("headline") or "", f6, w - 2)
    pages = [lines[i:i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)][:MAX_PAGES]
    faces = [VBox([Text(line, f6, LIGHT) for line in page], spacing=LINE_GAP, align="start") for page in pages]
    if faces:
        box_h = LINES_PER_PAGE * LINE_H + (LINES_PER_PAGE - 1) * LINE_GAP
        items.append((Cycle(faces, period=page_seconds, swap=PAGE_SWAP, h_align="start", v_align="start"), 1, 41, w - 2, box_h))
    return Absolute(items)


def compact_card(alert: dict[str, Any], w: int, h: int, f6: Any, now: datetime, counter: str) -> Absolute:
    """Bar, hazard, end time, area — four rows for the 32-tall panels."""
    color = level_color(alert)
    name = _name(alert)
    items: list[tuple[Any, int, int, int, int]] = [
        (_bar_node(alert, w, f6, counter), 0, 0, w, BAR_H),
        (Marquee(Text(name, fit_font(name, NAME_FONT, w - 2, 8, 6), WHITE), w - 2, speed=MARQUEE_SPEED, h_align="start"), 1, 9, w - 2, 8),
        (Marquee(Text(alert.get("area") or "", f6, GRAY), w - 2, speed=MARQUEE_SPEED, h_align="start"), 1, h - LINE_H - 1, w - 2, LINE_H),
    ]
    until = until_text(alert.get("expires"), now)
    if until:
        items.append((Anchor(Text(until, f6, color), h="start"), 1, 18, w - 2, LINE_H))
    return Absolute(items)


def card(alert: dict[str, Any], ctx: BoardContext, counter: str = "", page_seconds: float = 4.0) -> Absolute:
    f6 = ctx.profile.label_font()
    if ctx.height < COMPACT_BELOW:
        return compact_card(alert, ctx.width, ctx.height, f6, ctx.now, counter)
    return full_card(alert, ctx.width, ctx.height, f6, ctx.now, counter, page_seconds)


class AlertsBoard(BaseBoard):
    key = "weather.alerts"
    title = "Weather alerts"
    config_model = AlertsBoardConfig
    requires = frozenset({ALERTS_KEY})

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []

    def enter(self, ctx: BoardContext, cfg: AlertsBoardConfig) -> None:
        # The source re-checks expiry every poll; between polls the board does it itself.
        self._items = [a for a in (ctx.snapshot.get(ALERTS_KEY) or []) if in_force(a, ctx.now)]

    def done(self, ctx: BoardContext, cfg: AlertsBoardConfig) -> bool:
        return not self._items or ctx.elapsed >= cfg.seconds_per_alert * len(self._items)

    def auto_seconds(self, ctx: BoardContext, cfg: AlertsBoardConfig) -> float:
        return cfg.seconds_per_alert * max(len(ctx.snapshot.get(ALERTS_KEY) or []), 1)

    def render(self, ctx: BoardContext, cfg: AlertsBoardConfig) -> Image.Image:
        if not self._items:
            self.enter(ctx, cfg)
        if not self._items:
            return render_tree(Text("NO WEATHER ALERTS", ctx.profile.label_font(), GRAY), ctx.width, ctx.height)
        idx = min(int(ctx.elapsed // cfg.seconds_per_alert), len(self._items) - 1)
        local = ctx.elapsed - idx * cfg.seconds_per_alert
        counter = f"{idx + 1}/{len(self._items)}" if len(self._items) > 1 else ""
        return render_tree(card(self._items[idx], ctx, counter, cfg.page_seconds), ctx.width, ctx.height, t=local)


class AlertBoard(SequenceMixin, EventBoard):
    key = "weather.alert"
    title = "Weather alert interrupt"
    config_model = AlertBoardConfig
    event_kinds = frozenset({EVENT_KIND})

    def matches(self, event: Event, cfg: AlertBoardConfig) -> bool:
        if not cfg.enabled or event.kind not in self.event_kinds:
            return False
        alert = event.payload.get("alert") or {}
        if LEVEL_RANK.get(alert.get("level"), len(LEVEL_RANK)) > LEVEL_RANK[cfg.min_level]:
            return False
        return cfg.interrupt_live_game or not event.payload.get("live_game")

    def build(self, ctx: BoardContext, cfg: AlertBoardConfig) -> Sequence:
        alert = (ctx.event.payload.get("alert") if ctx.event else None) or {}
        frames = [render_tree(card(alert, ctx), ctx.width, ctx.height, t=i / ctx.fps) for i in range(int(cfg.duration * ctx.fps))]
        still = frames[0] if frames else Image.new("RGB", (ctx.width, ctx.height))
        return Sequence(ctx.fps).flash(level_color(alert), times=2, secs=FLASH_SECONDS).frames(frames).build(still)
