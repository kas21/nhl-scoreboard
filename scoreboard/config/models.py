"""Typed application configuration.

Every setting lives here (or in a plugin's ``config_model``). The JSON Schema
exported from these models drives the web UI, so a field added here shows up
in the browser with no extra work.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..logovariants import VARIANTS as LOGO_VARIANTS

GpioMapping = Literal["regular", "adafruit-hat", "adafruit-hat-pwm", "regular-pi1", "classic", "classic-pi1"]
LogoVariant = Literal[tuple(LOGO_VARIANTS)]  # type: ignore[valid-type]


ADVANCED: dict[str, Any] = {"advanced": True}
"""Field marker: shown in the web UI only when "Advanced" is ticked.

For settings that are real but rarely touched — panel driver tuning, poll
cadences — so the common fields aren't buried among them.
"""


def edited_on(page: str, label: str) -> dict[str, Any]:
    """Model marker: this plugin has a page of its own in the web UI.

    Some settings are not scalars — a per-holiday row is a toggle, a rename and a picture
    upload at once — so the generated form cannot edit them, and "edit config.json" is a
    dead end once a real editor exists. A model carrying this gets a link to it; its
    simple fields still appear in the generated form as usual.
    """
    return {"editor": {"page": page, "label": label}}


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
    pwm_bits: int = Field(11, ge=1, le=11, json_schema_extra=ADVANCED)
    pwm_lsb_nanoseconds: int = Field(130, ge=50, le=3000, json_schema_extra=ADVANCED)
    pwm_dither_bits: int = Field(0, ge=0, le=2, json_schema_extra=ADVANCED)
    slowdown_gpio: int = Field(4, ge=0, le=5, json_schema_extra=ADVANCED)
    limit_refresh: int = Field(0, ge=0, description="Cap refresh Hz (0 = unlimited)", json_schema_extra=ADVANCED)
    scan_mode: int = Field(0, ge=0, le=1, json_schema_extra=ADVANCED)
    row_addr_type: int = Field(0, ge=0, le=5, json_schema_extra=ADVANCED)
    multiplexing: int = Field(0, ge=0, le=18, json_schema_extra=ADVANCED)
    panel_type: str = Field("", description="e.g. FM6126A, leave blank for most panels", json_schema_extra=ADVANCED)
    rgb_sequence: str = Field("RGB", pattern=r"^[RGB]{3}$", json_schema_extra=ADVANCED)
    pixel_mapper: str = Field("", description="e.g. 'U-mapper;Rotate:90'", json_schema_extra=ADVANCED)
    drop_privileges: bool = Field(False, description="Appliance mode keeps root", json_schema_extra=ADVANCED)


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

    offseason: tuple[PlaylistEntry, ...] = (
        PlaylistEntry(board="season.countdown", duration=12),
        PlaylistEntry(board="clock", duration=10),
        PlaylistEntry(board="weather.current", duration=None),
        PlaylistEntry(board="holidays.countdown", duration=None),
        PlaylistEntry(board="flights.nearby", duration=None),
        PlaylistEntry(board="nhl.team_summary", duration=10),
    )
    offday: tuple[PlaylistEntry, ...] = (
        PlaylistEntry(board="nhl.team_summary", duration=10),
        PlaylistEntry(board="clock", duration=10),
        PlaylistEntry(board="nhl.ticker", duration=None),
        PlaylistEntry(board="nhl.standings", duration=None),
        PlaylistEntry(board="holidays.countdown", duration=None),
    )
    pregame: tuple[PlaylistEntry, ...] = (
        PlaylistEntry(board="nhl.game", duration=15),
        PlaylistEntry(board="nfl.game", duration=15),
        PlaylistEntry(board="ncaaf.game", duration=15),
        PlaylistEntry(board="mlb.game", duration=15),
        PlaylistEntry(board="nhl.ticker", duration=None),
        PlaylistEntry(board="clock", duration=10),
    )
    live: tuple[PlaylistEntry, ...] = (
        PlaylistEntry(board="nhl.game", duration=None),
        PlaylistEntry(board="nfl.game", duration=None),
        PlaylistEntry(board="ncaaf.game", duration=None),
        PlaylistEntry(board="mlb.game", duration=None),
    )
    intermission: tuple[PlaylistEntry, ...] = (
        PlaylistEntry(board="nhl.game", duration=15),
        PlaylistEntry(board="nfl.game", duration=15),
        PlaylistEntry(board="ncaaf.game", duration=15),
        PlaylistEntry(board="mlb.game", duration=15),
        PlaylistEntry(board="nhl.ticker", duration=None),
        PlaylistEntry(board="nhl.standings", duration=None),
    )
    postgame: tuple[PlaylistEntry, ...] = (
        PlaylistEntry(board="nhl.game", duration=20),
        PlaylistEntry(board="nfl.game", duration=20),
        PlaylistEntry(board="ncaaf.game", duration=20),
        PlaylistEntry(board="mlb.game", duration=20),
        PlaylistEntry(board="nhl.ticker", duration=None),
        PlaylistEntry(board="nhl.standings", duration=None),
        PlaylistEntry(board="clock", duration=10),
    )


class TransitionConfig(FrozenModel):
    """How the display changes from one board to the next."""

    style: Literal["none", "fade", "slide_left", "slide_right", "slide_up", "slide_down", "wipe", "blinds"] = "fade"
    duration: float = Field(0.5, ge=0.1, le=3.0, description="Seconds")


class LogosConfig(FrozenModel):
    """Which artwork each team uses.

    Some clubs' primary logo is a wordmark, or a dark mark on a dark panel, and turns to
    mush at the sizes a matrix has to work with. The audited defaults fix the handful of
    NHL teams where that happens; ``overrides`` lets you disagree, per team.
    """

    use_curated_defaults: bool = Field(
        True, description="Use the audited per-team logo picks for teams whose default is unreadable on a panel"
    )
    overrides: dict[str, LogoVariant] = Field(
        default_factory=dict,
        description="Per-team logo choice, keyed '<sport>:<ABBREV>', e.g. {'nhl:WSH': 'secondary_on_black'}",
    )


class SportsConfig(FrozenModel):
    priority: list[Literal["nhl", "nfl", "ncaaf", "mlb"]] = Field(["nhl", "nfl", "ncaaf", "mlb"], description="When two sports have a game, which wins the screen (live games always win)")


class WebConfig(FrozenModel):
    port: int = Field(8080, ge=1, le=65535)
    host: str = Field("0.0.0.0", json_schema_extra=ADVANCED)
    allowed_hosts: list[str] = Field(
        default_factory=list,
        description="Extra hostnames the UI may be reached at. localhost, this machine's "
                    "name, <name>.local and any IP address already work; add a name here only "
                    "if you front the scoreboard with your own DNS entry",
        json_schema_extra=ADVANCED,
    )
    preview_fps: int = Field(30, ge=1, le=60, description="Browser preview frame rate (only costs CPU while someone is watching)")
    update_check_hours: int = Field(24, ge=0, le=168, description="How often to look for updates on GitHub (0 = never)")
    allow_unowned_checkout: bool = Field(
        False,
        description="Let the updater pull into a checkout owned by a different user than the "
                    "service runs as. Updating runs 'pip install -e .', so that user can run code "
                    "as this service — only turn this on for a box where you are that user",
        json_schema_extra=ADVANCED,
    )


CONFIG_VERSION = 2


class AppConfig(FrozenModel):
    """Root config document, persisted as config.json."""

    version: int = Field(CONFIG_VERSION, description="Config schema version (managed by the app)")
    setup_complete: bool = False
    display: DisplayConfig = DisplayConfig()
    location: LocationConfig = LocationConfig()
    brightness: BrightnessConfig = BrightnessConfig()
    playlists: Playlists = Playlists()
    transition: TransitionConfig = TransitionConfig()
    sports: SportsConfig = SportsConfig()
    logos: LogosConfig = LogosConfig()
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
