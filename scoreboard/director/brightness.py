"""Brightness from config + time of day (fixed / sunrise-sunset / fixed hours)."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from astral import LocationInfo
from astral.sun import sun

from ..config.models import BrightnessConfig, LocationConfig


def _parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _in_window(now: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now < end
    return now >= start or now < end   # wraps midnight


def is_night(now: datetime, cfg: BrightnessConfig, loc: LocationConfig) -> bool:
    if cfg.mode == "fixed":
        return False
    if cfg.mode == "hours":
        return _in_window(now.time(), _parse_hhmm(cfg.night_start), _parse_hhmm(cfg.night_end))
    if loc.latitude is None or loc.longitude is None:
        return _in_window(now.time(), _parse_hhmm(cfg.night_start), _parse_hhmm(cfg.night_end))
    info = LocationInfo(latitude=loc.latitude, longitude=loc.longitude, timezone=loc.timezone)
    try:
        s = sun(info.observer, date=now.date(), tzinfo=now.tzinfo)
    except ValueError:            # polar day/night
        return False
    offset = timedelta(minutes=cfg.sunset_offset_minutes)
    return now >= s["sunset"] + offset or now < s["sunrise"] - offset


def brightness_for(now: datetime, cfg: BrightnessConfig, loc: LocationConfig, live: bool) -> int:
    if live and cfg.keep_bright_when_live:
        return cfg.day
    return cfg.night if is_night(now, cfg, loc) else cfg.day
