import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from scoreboard.boards.base import BoardContext
from scoreboard.data import SnapshotStore
from scoreboard.data.source import SourceContext
from scoreboard.extras.weather.board import (
    HILO_GAP,
    WeatherBoard,
    WeatherBoardConfig,
    icon_image,
    temp_color,
    today_entry,
)
from scoreboard.extras.weather.source import WeatherConfig, WeatherSource, describe, normalize
from scoreboard.render import load_font
from scoreboard.render.profiles import profile_for
from scoreboard.render.text import text_size

FIX = Path(__file__).parent / "fixtures" / "weather" / "open_meteo.json"


def test_normalize_units_and_codes():
    payload = json.loads(FIX.read_text())
    cur, daily = normalize(payload, WeatherConfig(units="imperial", label="Toronto"))
    assert cur["temp"] == 74 and cur["units"] == {"temp": "F", "speed": "mph"} and cur["label"] == "Toronto"
    assert cur["short"] == "CLR" and cur["icon"] == "clear"
    assert len(daily) == 4 and daily[1]["hi"] == 81 and daily[1]["pop"] == 32
    cur_m, _ = normalize(payload, WeatherConfig(units="metric"))
    assert cur_m["temp"] == 23 and cur_m["wind"] == 14
    assert describe(95)["icon"] == "storm" and describe(0, is_day=False)["icon"] == "night"


@pytest.mark.asyncio
async def test_source_publishes():
    store = SnapshotStore()
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(url__regex=r"https://api\.open-meteo\.com/.*").mock(return_value=httpx.Response(200, json=json.loads(FIX.read_text())))
        ctx = SourceContext("weather", store, lambda: WeatherConfig(), http)
        ctx.location, ctx.timezone = (43.65, -79.38), "America/Toronto"
        task = asyncio.create_task(WeatherSource().run(ctx))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if store.get().has("weather.current", "weather.daily"):
                break
        task.cancel()
    assert store.get().get("weather.current")["temp"] == 74


def test_board_renders_all_sizes():
    cur, daily = normalize(json.loads(FIX.read_text()), WeatherConfig(label="Toronto"))
    store = SnapshotStore(); store.publish("weather.current", cur); snap = store.publish("weather.daily", daily)
    now = datetime(2026, 8, 26, 12, tzinfo=ZoneInfo("America/Toronto"))
    for w, h in [(128, 64), (64, 32), (128, 32)]:
        ctx = BoardContext(snapshot=snap, profile=profile_for(w, h), width=w, height=h, fps=30, now=now, elapsed=2.0)
        img = WeatherBoard().render(ctx, WeatherBoardConfig())
        assert img.size == (w, h) and img.getbbox() is not None
    assert icon_image("rain", 14).size[0] > 4
    assert temp_color(90, True) == (255, 150, 80) and temp_color(-5, False) == (120, 180, 255)


def _weather_ctx(now, label="Toronto", daily=True, elapsed=2.0):
    cur, days = normalize(json.loads(FIX.read_text()), WeatherConfig(label=label))
    store = SnapshotStore()
    store.publish("weather.current", cur)
    snap = store.publish("weather.daily", days if daily else [])
    return BoardContext(snapshot=snap, profile=profile_for(128, 64), width=128, height=64, fps=30, now=now, elapsed=elapsed)


def _lit(img, box):
    """Number of lit pixels in an (x0, y0, x1, y1) box."""
    return sum(1 for px in img.crop(box).convert("L").getdata() if px > 30)


def _hilo_box(cur, daily, today):
    """Where the board is expected to draw today's hi/lo, from the same measurements it uses."""
    d = today_entry(daily, today)
    f6, big = load_font("pl", 6), load_font("pl", 12)
    tw = text_size(f"{cur['temp']}{'F' if cur['units']['temp'] == 'F' else 'C'}", big)[0]
    hlw = text_size(f"{d['hi']}/{d['lo']}", f6)[0]
    x = 128 - 1 - tw - HILO_GAP - hlw
    return (x, 1, x + hlw, 7)


def test_today_entry_matches_on_date_not_position():
    _, daily = normalize(json.loads(FIX.read_text()), WeatherConfig())
    assert today_entry(daily, date(2026, 8, 26))["hi"] == 74      # 23.3C -> 74F
    assert today_entry(daily, date(2026, 8, 27))["hi"] == 81
    assert today_entry(daily, date(2030, 1, 1)) is None


def test_todays_hilo_renders_beside_the_current_temp():
    now = datetime(2026, 8, 26, 12, tzinfo=ZoneInfo("America/Toronto"))
    cur, daily = normalize(json.loads(FIX.read_text()), WeatherConfig(label="Toronto"))
    box = _hilo_box(cur, daily, now.date())
    assert _lit(WeatherBoard().render(_weather_ctx(now), WeatherBoardConfig()), box) > 0
    # With no daily data the same region stays dark, and the label reclaims the width.
    assert _lit(WeatherBoard().render(_weather_ctx(now, daily=False), WeatherBoardConfig()), box) == 0


def test_longest_label_still_clears_todays_hilo():
    """Worst case: a 16-char label and the widest temp must not run into the hi/lo."""
    now = datetime(2026, 8, 26, 12, tzinfo=ZoneInfo("America/Toronto"))
    cur, daily = normalize(json.loads(FIX.read_text()), WeatherConfig(label="ABCDEFGHIJKLMNOP"))
    x0, y0, x1, y1 = _hilo_box(cur, daily, now.date())
    img = WeatherBoard().render(_weather_ctx(now, label="ABCDEFGHIJKLMNOP"), WeatherBoardConfig())
    assert _lit(img, (x0, y0, x1, y1)) > 0                  # hi/lo still drawn
    assert _lit(img, (x0 - 3, y0, x0, y1)) == 0             # label stops short of it
    assert _lit(img, (x1, y0, x1 + HILO_GAP, y1)) == 0      # and it stops short of the temp

