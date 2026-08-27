"""Holiday countdown data: upcoming public holidays (``holidays`` package) plus custom dates."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from ...config.models import ADVANCED
from ...data.source import SourceContext

log = logging.getLogger(__name__)
IMAGES = Path(__file__).parent.parent.parent / "assets" / "holidays"


class CustomHoliday(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(max_length=40)
    date: str = Field(description="YYYY-MM-DD, or MM-DD for a yearly date", pattern=r"^(\d{4}-)?\d{2}-\d{2}$")
    enabled: bool = True


class HolidaysConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Holidays")
    country: str = Field("US", min_length=2, max_length=2, description="ISO country code, e.g. US, CA, GB")
    subdivision: str = Field("", max_length=5, description="State / province code (optional, e.g. NY, ON)")
    horizon_days: int = Field(90, ge=1, le=365, description="How far ahead to look")
    custom: list[CustomHoliday] = Field([], description="Your own dates (birthdays, puck drop, ...)")
    disabled: list[str] = Field([], description="Holiday names to hide")
    refresh_seconds: int = Field(3600, ge=300, le=86400, json_schema_extra=ADVANCED)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def upcoming(cfg: HolidaysConfig, today: date) -> list[dict[str, Any]]:
    """Pure: the holiday list for ``today`` (sorted, de-duplicated, within the horizon)."""
    import holidays as holidays_lib

    found: dict[tuple[date, str], dict[str, Any]] = {}
    years = {today.year, (today + timedelta(days=cfg.horizon_days)).year}
    try:
        cal = holidays_lib.country_holidays(cfg.country.upper(), subdiv=cfg.subdivision.upper() or None, years=years)
    except Exception as exc:
        log.warning("holidays: cannot build calendar for %s/%s: %s", cfg.country, cfg.subdivision, exc)
        cal = {}
    disabled = {d.lower() for d in cfg.disabled}
    for day, name in cal.items():
        for part in str(name).split("; "):
            if part.lower() not in disabled:
                found[(day, part)] = {"name": part, "date": day.isoformat(), "custom": False}
    for c in cfg.custom:
        if not c.enabled:
            continue
        for y in years:
            iso = c.date if len(c.date) == 10 else f"{y}-{c.date}"
            try:
                day = date.fromisoformat(iso)
            except ValueError:
                continue
            found[(day, c.name)] = {"name": c.name, "date": day.isoformat(), "custom": True}
    out = []
    for (day, _), item in sorted(found.items()):
        days = (day - today).days
        if 0 <= days <= cfg.horizon_days:
            image = IMAGES / f"{slug(item['name'])}.png"
            out.append({**item, "days": days, "image": image.name if image.exists() else None})
    return out


class HolidaysSource:
    key: ClassVar[str] = "holidays"
    config_model: ClassVar[type[BaseModel]] = HolidaysConfig

    async def run(self, ctx: SourceContext) -> None:
        while True:
            cfg: HolidaysConfig = ctx.config  # type: ignore[assignment]
            tz = ctx.timezone
            try:
                today = datetime.now(ZoneInfo(tz)).date() if tz else datetime.now().astimezone().date()
            except Exception:
                today = date.today()
            ctx.publish(await asyncio.to_thread(upcoming, cfg, today), subkey="upcoming")
            await asyncio.sleep(cfg.refresh_seconds)
