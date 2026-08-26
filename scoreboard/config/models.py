"""Typed application configuration.

Every setting lives here (or in a plugin's ``config_model``). The JSON Schema
exported from these models drives the web UI, so a field added here shows up
in the browser with no extra work.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

GpioMapping = Literal["regular", "adafruit-hat", "adafruit-hat-pwm", "regular-pi1", "classic", "classic-pi1"]


class FrozenModel(BaseModel):
    """Immutable base: config objects are replaced, never mutated."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class DisplayConfig(FrozenModel):
    """LED panel wiring. Mirrors rpi-rgb-led-matrix options."""

    width: int = Field(128, ge=8, le=1024, description="Total pixels wide (cols x chain)")
    height: int = Field(64, ge=8, le=1024, description="Total pixels high (rows x parallel)")
    chain: int = Field(1, ge=1, le=16, description="Panels daisy-chained")
    parallel: int = Field(1, ge=1, le=3, description="Parallel chains")
    gpio_mapping: GpioMapping = Field("adafruit-hat-pwm", description="Hardware mapping")
    fps: int = Field(30, ge=5, le=60, description="Render loop frame rate")
    pwm_bits: int = Field(11, ge=1, le=11)
    pwm_lsb_nanoseconds: int = Field(130, ge=50, le=3000)
    pwm_dither_bits: int = Field(0, ge=0, le=2)
    slowdown_gpio: int = Field(4, ge=0, le=5)
    limit_refresh: int = Field(0, ge=0, description="Cap refresh Hz (0 = unlimited)")
    scan_mode: int = Field(0, ge=0, le=1)
    row_addr_type: int = Field(0, ge=0, le=5)
    multiplexing: int = Field(0, ge=0, le=18)
    panel_type: str = Field("", description="e.g. FM6126A, leave blank for most panels")
    rgb_sequence: str = Field("RGB", pattern=r"^[RGB]{3}$")
    pixel_mapper: str = Field("", description="e.g. 'U-mapper;Rotate:90'")
    drop_privileges: bool = Field(False, description="Appliance mode keeps root")


class LocationConfig(FrozenModel):
    timezone: str = Field("America/New_York", description="IANA timezone")
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)


class BrightnessConfig(FrozenModel):
    mode: Literal["fixed", "sun", "hours"] = "fixed"
    day: int = Field(80, ge=1, le=100, description="Daytime brightness")
    night: int = Field(30, ge=0, le=100, description="Night brightness (0 = off)")
    night_start: str = Field("22:00", pattern=r"^\d{2}:\d{2}$", description="For 'hours' mode")
    night_end: str = Field("07:00", pattern=r"^\d{2}:\d{2}$")
    sunset_offset_minutes: int = Field(0, ge=-180, le=180, description="For 'sun' mode")
    keep_bright_when_live: bool = Field(True, description="Ignore night dimming during live games")


class PlaylistEntry(FrozenModel):
    board: str = Field(description="Board key, e.g. 'clock'")
    duration: float | None = Field(15.0, ge=1, description="Seconds; null = board decides")
    enabled: bool = True


class Playlists(FrozenModel):
    """What to show in each application state, in order."""

    offday: tuple[PlaylistEntry, ...] = (
        PlaylistEntry(board="nhl.team_summary", duration=10),
        PlaylistEntry(board="clock", duration=10),
        PlaylistEntry(board="nhl.ticker", duration=None),
        PlaylistEntry(board="nhl.standings", duration=None),
    )
    pregame: tuple[PlaylistEntry, ...] = (
        PlaylistEntry(board="nhl.game", duration=15),
        PlaylistEntry(board="nhl.ticker", duration=None),
        PlaylistEntry(board="clock", duration=10),
    )
    live: tuple[PlaylistEntry, ...] = (PlaylistEntry(board="nhl.game", duration=None),)
    intermission: tuple[PlaylistEntry, ...] = (
        PlaylistEntry(board="nhl.game", duration=15),
        PlaylistEntry(board="nhl.ticker", duration=None),
        PlaylistEntry(board="nhl.standings", duration=None),
    )
    postgame: tuple[PlaylistEntry, ...] = (
        PlaylistEntry(board="nhl.game", duration=20),
        PlaylistEntry(board="nhl.ticker", duration=None),
        PlaylistEntry(board="nhl.standings", duration=None),
        PlaylistEntry(board="clock", duration=10),
    )


class TransitionConfig(FrozenModel):
    """How the display changes from one board to the next."""

    style: Literal["none", "fade", "slide_left", "slide_right", "slide_up", "slide_down", "wipe", "blinds"] = "fade"
    duration: float = Field(0.5, ge=0.1, le=3.0, description="Seconds")


class WebConfig(FrozenModel):
    port: int = Field(8080, ge=1, le=65535)
    host: str = "0.0.0.0"


CONFIG_VERSION = 1


class AppConfig(FrozenModel):
    """Root config document, persisted as config.json."""

    version: int = Field(CONFIG_VERSION, description="Config schema version (managed by the app)")
    setup_complete: bool = False
    display: DisplayConfig = DisplayConfig()
    location: LocationConfig = LocationConfig()
    brightness: BrightnessConfig = BrightnessConfig()
    playlists: Playlists = Playlists()
    transition: TransitionConfig = TransitionConfig()
    web: WebConfig = WebConfig()
    boards: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Per-board settings, validated by each board's model"
    )
    sources: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Per-source settings, validated by each source's model"
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict with ``patch`` recursively merged over ``base``."""
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
