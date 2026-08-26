import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from scoreboard.boards.base import BoardContext
from scoreboard.data import SnapshotStore
from scoreboard.data.source import SourceContext
from scoreboard.extras.weather.board import WeatherBoard, WeatherBoardConfig, icon_image, temp_color
from scoreboard.extras.weather.source import WeatherConfig, WeatherSource, describe, normalize
from scoreboard.render.profiles import profile_for

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
