"""Weather board — port of the old layout: current block (label/temp with sheen, description/feels-like,
humidity/wind) above a divider and a 3-day forecast strip with Weather Icons glyphs."""
from __future__ import annotations

from datetime import date

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from ...boards.base import BaseBoard, BoardContext
from ...render import Absolute, Cycle, Img, Sheen, Slide, Text, load_font, render_tree
from ...render.anim import quintic_out
from ...render.text import text_size

WHITE = (255, 255, 255)
GRAY = (180, 180, 180)
DIM = (80, 80, 80)
HUMIDITY = (100, 150, 255)
WIND = (100, 255, 150)
DIVIDER = (60, 60, 60)
HILO_GAP = 3          # px between today's hi/lo and the current temp beside it
PRECIP_SWAP = 0.4     # seconds the forecast readout takes to roll between its two faces
# Icons that draw falling weather. Open-Meteo's daily weather_code is the day's dominant
# condition while pop comes from ensemble spread, so a drizzle code can carry a low chance
# — a day drawn as wet reports its chance regardless of where the probability landed.
PRECIP_ICONS = frozenset({"rain", "showers", "storm", "snow", "sleet"})
ICON_COLORS = {"clear": (255, 220, 50), "night": (255, 220, 50), "partly": (200, 200, 200), "cloudy": (150, 150, 150),
               "rain": (80, 130, 255), "showers": (80, 130, 255), "storm": (180, 100, 255), "snow": (230, 230, 255),
               "sleet": (200, 210, 255), "fog": (120, 120, 120)}
GLYPHS = {"clear": "", "night": "", "partly": "", "cloudy": "", "rain": "",
          "showers": "", "storm": "", "snow": "", "sleet": "", "fog": ""}


class WeatherBoardConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Weather board")
    duration: float = Field(15.0, ge=3, le=60)
    show_forecast: bool = True
    precip_threshold: int = Field(20, ge=0, le=100, description="Forecast days at or above this chance of precipitation (%) alternate between hi/lo and the chance. Days whose icon shows rain or snow always alternate, whatever this is set to")
    precip_hold_seconds: float = Field(3.0, ge=1, le=15, description="How long the forecast shows each of hi/lo and the chance of precipitation")


def temp_color(t: int | None, imperial: bool) -> tuple[int, int, int]:
    if t is None:
        return WHITE
    c = (t - 32) * 5 / 9 if imperial else t
    if c <= 0:
        return (120, 180, 255)
    if c <= 15:
        return (0, 200, 200)
    if c <= 25:
        return (255, 255, 255)
    return (255, 150, 80)


def today_entry(daily: list[dict], today: date) -> dict | None:
    """Today's row from the daily forecast.

    Open-Meteo starts ``daily`` at today, but match on the date rather than trusting
    position — a stale payload held across midnight would otherwise report yesterday.
    """
    iso = today.isoformat()
    return next((d for d in daily if d.get("date") == iso), None)


def icon_image(key: str, size: int) -> Image.Image:
    font = load_font("weathericons.ttf", size)
    glyph = GLYPHS.get(key, GLYPHS["cloudy"])
    left, top, right, bottom = font.getbbox(glyph, anchor="la")
    img = Image.new("RGBA", (max(right - left, 1), max(bottom - top, 1)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    d.text((-left, -top), glyph, font=font, fill=ICON_COLORS.get(key, GRAY), anchor="la")
    return img


class WeatherBoard(BaseBoard):
    key = "weather.current"
    title = "Weather"
    config_model = WeatherBoardConfig
    requires = frozenset({"weather.current"})

    def done(self, ctx: BoardContext, cfg: WeatherBoardConfig) -> bool:
        return ctx.elapsed >= cfg.duration

    def auto_seconds(self, ctx: BoardContext, cfg: WeatherBoardConfig) -> float:
        return cfg.duration

    def render(self, ctx: BoardContext, cfg: WeatherBoardConfig) -> Image.Image:
        cur = ctx.snapshot.get("weather.current") or {}
        daily = ctx.snapshot.get("weather.daily") or []
        w, h = ctx.width, ctx.height
        f6, big = ctx.profile.label_font(), load_font("pl", 12)
        imp = cur.get("units", {}).get("temp") == "F"
        unit_txt = "F" if imp else "C"     # bitmap font has no degree sign
        items = []
        if h < 48:
            return self._compact(cur, w, h, unit_txt, ctx)
        # -- current block --
        temp = f"{cur.get('temp', '--')}{unit_txt}"
        tnode = Sheen(Text(temp, big, temp_color(cur.get("temp"), imp)), period=3.0, band=10, strength=0.6, delay=1.0)
        tw = text_size(temp, big)[0]
        # Today's hi/lo caps the temperature column, on the label row so nothing else
        # moves. The label gives up the width it takes, so the two cannot collide.
        today = today_entry(daily, ctx.now.date())
        hilo = f"{today.get('hi', '--')}/{today.get('lo', '--')}" if today else ""
        hlw = text_size(hilo, f6)[0] if hilo else 0
        label_w = w - tw - 4 - (hlw + HILO_GAP if hilo else 0)
        items.append((Slide(Text(cur.get("label", "WEATHER")[:16].upper(), f6, WHITE), 0.3, "left", easing=quintic_out, h_align="start"), 1, 1, label_w, 6))
        items.append((tnode, w - 1 - tw, 0, tw, 12))
        if hilo:
            items.append((Slide(Text(hilo, f6, GRAY), 0.3, "left", delay=0.05, easing=quintic_out, h_align="end"), w - 1 - tw - HILO_GAP - hlw, 1, hlw, 6))
        icon = icon_image(cur.get("icon", "cloudy"), 14)
        items.append((Slide(Img(icon), 0.4, "left", easing=quintic_out, h_align="start"), 1, 9, icon.width, icon.height))
        desc = (cur.get("desc") or "")[:14 if w < 128 else 22]
        items.append((Slide(Text(desc, f6, ICON_COLORS.get(cur.get("icon", ""), GRAY)), 0.3, "up", delay=0.1, easing=quintic_out, h_align="start"), icon.width + 3, 13, w - icon.width - 4 - 40, 6))
        feels = f"Feels {cur.get('feels', '--')}{unit_txt}"
        fw = text_size(feels, f6)[0]
        items.append((Text(feels, f6, GRAY), w - 1 - fw, 13, fw, 6))
        hum = f"Hum {cur.get('humidity', '--')}%"
        items.append((Slide(Text(hum, f6, HUMIDITY), 0.3, "up", delay=0.15, easing=quintic_out, h_align="start"), icon.width + 3, 22, 50, 6))
        wind = f"Wind {cur.get('wind', '--')}{cur.get('units', {}).get('speed', '')}"
        ww = text_size(wind, f6)[0]
        items.append((Slide(Text(wind, f6, WIND), 0.3, "up", delay=0.2, easing=quintic_out, h_align="end"), w - 1 - ww, 22, ww, 6))
        # -- forecast strip --
        if cfg.show_forecast and daily and h >= 48:
            y0 = 30
            items.append((Img(Image.new("RGBA", (w, 1), (*DIVIDER, 255))), 0, y0, w, 1))
            days = [d for d in daily if d["date"] > date.today().isoformat()][:3] or daily[1:4]
            col = w // max(len(days), 1)
            for i, d in enumerate(days):
                x = i * col
                try:
                    name = date.fromisoformat(d["date"]).strftime("%a").upper()
                except ValueError:
                    name = "---"
                nw = text_size(name, f6)[0]
                items.append((Slide(Text(name, f6, WHITE), 0.3, "down", delay=0.1 * i, easing=quintic_out), x + (col - nw) // 2, y0 + 3, nw, 6))
                ic = icon_image(d.get("icon", "cloudy"), 12)
                items.append((Slide(Img(ic), 0.3, "down", delay=0.1 * i + 0.05, easing=quintic_out), x + (col - ic.width) // 2, y0 + 10, ic.width, ic.height))
                # A wet day carries two readouts in one slot; a dry one just shows hi/lo.
                hilo = f"{d.get('hi', '--')}/{d.get('lo', '--')}"
                pop = d.get("pop")
                wet = d.get("icon") in PRECIP_ICONS or (pop is not None and pop >= cfg.precip_threshold)
                faces, hw = [Text(hilo, f6, GRAY)], text_size(hilo, f6)[0]
                if wet and pop is not None:
                    chance = f"{pop}%"
                    faces.append(Text(chance, f6, HUMIDITY))
                    hw = max(hw, text_size(chance, f6)[0])       # a fixed box, so neither face shifts
                readout = Cycle(faces, period=cfg.precip_hold_seconds, swap=PRECIP_SWAP)
                items.append((Slide(readout, 0.3, "down", delay=0.1 * i + 0.1, easing=quintic_out), x + (col - hw) // 2, h - 8, hw, 6))
        return render_tree(Absolute(items), w, h, t=ctx.elapsed)

    def _compact(self, cur: dict, w: int, h: int, unit_txt: str, ctx: BoardContext) -> Image.Image:
        """64x32: label + temp, icon + description, humidity / wind."""
        f6, big = ctx.profile.label_font(), load_font("pl", 12)
        imp = unit_txt == "F"
        temp = f"{cur.get('temp', '--')}{unit_txt}"
        tw = text_size(temp, big)[0]
        items = [
            (Slide(Text(cur.get("label", "WEATHER")[:9].upper(), f6, WHITE), 0.3, "left", easing=quintic_out, h_align="start"), 1, 1, w - tw - 3, 6),
            (Sheen(Text(temp, big, temp_color(cur.get("temp"), imp)), period=3.0, band=10, strength=0.6, delay=1.0), w - 1 - tw, 0, tw, 12),
        ]
        icon = icon_image(cur.get("icon", "cloudy"), 11)
        items.append((Img(icon), 1, 13, icon.width, icon.height))
        desc = (cur.get("short") or "")
        items.append((Text(desc, f6, ICON_COLORS.get(cur.get("icon", ""), GRAY)), icon.width + 3, 15, 30, 6))
        hw = f"H{cur.get('humidity', '--')}% W{cur.get('wind', '--')}"
        items.append((Text(hw, f6, GRAY), 1, h - 7, w - 2, 6))
        return render_tree(Absolute(items), w, h, t=ctx.elapsed)
