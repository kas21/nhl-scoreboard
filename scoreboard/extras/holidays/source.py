"""Holiday countdown data: upcoming public holidays (``holidays`` package) plus custom dates."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...config.models import ADVANCED, edited_on
from ...data.source import SourceContext
from .images import IMAGES, base_name, image_path, slug, uploaded  # noqa: F401  (re-exported)

if TYPE_CHECKING:
    from ...config.models import AppConfig
    from ...data.store import SnapshotStore

log = logging.getLogger(__name__)

# The library defaults to PUBLIC only, which is far narrower than what a scoreboard
# wants to count down to: it omits Halloween, Groundhog Day and Mother's / Father's Day
# in the US (all of which we ship art for), and Victoria Day and the National Day for
# Truth and Reconciliation in Canada, which are federally GOVERNMENT days. Categories a
# country does not support are dropped rather than raising.
CATEGORIES = ("public", "unofficial", "optional", "government")

# A picture name is a filename stem; see images.py for why it is this narrow.
SLUG_PATTERN = r"^[a-z0-9_]*$"


class HolidayOverride(BaseModel):
    """What you changed about a holiday the calendar already knows about."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: bool = True
    display: str = Field("", max_length=40, description="Show this name instead of the official one")
    image: str = Field("", max_length=64, pattern=SLUG_PATTERN, description="Picture to use instead of the default")


class CustomHoliday(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(max_length=40)
    date: str = Field(description="YYYY-MM-DD, or MM-DD for a yearly date", pattern=r"^(\d{4}-)?\d{2}-\d{2}$")
    enabled: bool = True
    image: str = Field("", max_length=64, pattern=SLUG_PATTERN,
                       description="Picture to use; blank uses one named after the holiday")


class HolidaysConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="Holidays",
                              json_schema_extra=edited_on("holidays", "Open the Holidays page"))
    country: str = Field("US", min_length=2, max_length=2, description="ISO country code, e.g. US, CA, GB")
    subdivision: str = Field("", max_length=5, description="State / province code (optional, e.g. NY, ON)")
    horizon_days: int = Field(90, ge=1, le=365, description="How far ahead to look")
    custom: list[CustomHoliday] = Field([], description="Your own dates (birthdays, puck drop, ...)")
    overrides: dict[str, HolidayOverride] = Field(
        {}, description="Per-holiday changes, keyed by the official name: hide it, rename it, repicture it"
    )
    refresh_seconds: int = Field(3600, ge=300, le=86400, json_schema_extra=ADVANCED)


def calendar_names(cfg: HolidaysConfig, years: set[int]) -> list[str]:
    """Every distinct holiday name in the configured calendar, in first-seen order."""
    return list(dict.fromkeys(_calendar(cfg, years).values()))


def _calendar(cfg: HolidaysConfig, years: set[int]) -> dict[date, str]:
    """Day -> holiday name. Empty (with a warning) if the country code is not one we know."""
    import holidays as holidays_lib

    country = cfg.country.upper()
    try:
        supported = holidays_lib.country_holidays(country, years=years).supported_categories
        cal = holidays_lib.country_holidays(
            country,
            subdiv=cfg.subdivision.upper() or None,
            years=years,
            categories=tuple(c for c in CATEGORIES if c in supported) or None,
        )
    except Exception as exc:
        log.warning("holidays: cannot build calendar for %s/%s: %s", cfg.country, cfg.subdivision, exc)
        return {}
    # Asking for several categories makes the library join a day's names with "; " —
    # in practice always spellings of the same holiday ("Birthday of Martin Luther
    # King, Jr.; Martin Luther King Jr. Day"). The last is the everyday name.
    return {day: str(name).split("; ")[-1].strip() for day, name in cal.items()}


def _display(name: str, override: HolidayOverride | None) -> str:
    return (override.display if override else "") or name


def _years(cfg: HolidaysConfig, today: date) -> set[int]:
    return {today.year, (today + timedelta(days=cfg.horizon_days)).year}


def _custom_days(entry: CustomHoliday, years: set[int]) -> list[date]:
    """A ``YYYY-MM-DD`` entry lands once; an ``MM-DD`` one repeats every year in range."""
    days = []
    for year in sorted(years):
        try:
            days.append(date.fromisoformat(entry.date if len(entry.date) == 10 else f"{year}-{entry.date}"))
        except ValueError:
            log.warning("holidays: %r has an impossible date %r", entry.name, entry.date)
        if len(entry.date) == 10:
            break
    return days


def upcoming(cfg: HolidaysConfig, today: date) -> list[dict[str, Any]]:
    """Pure: the holiday list for ``today`` (sorted, de-duplicated, within the horizon)."""
    years = _years(cfg, today)
    found: dict[tuple[date, str], dict[str, Any]] = {}
    for day, name in _calendar(cfg, years).items():
        override = cfg.overrides.get(name)
        if override is not None and not override.enabled:
            continue
        found[(day, name)] = {"name": name, "display": _display(name, override), "date": day.isoformat(),
                              "custom": False, "image": image_path(name, override.image if override else "")}
    for entry in cfg.custom:
        override = cfg.overrides.get(entry.name)
        if not entry.enabled or (override is not None and not override.enabled):
            continue
        for day in _custom_days(entry, years):
            found[(day, entry.name)] = {"name": entry.name, "display": _display(entry.name, override),
                                        "date": day.isoformat(), "custom": True,
                                        "image": image_path(entry.name, entry.image)}
    out = []
    for (day, _), item in sorted(found.items()):
        days = (day - today).days
        if 0 <= days <= cfg.horizon_days:
            out.append({**item, "days": days})
    return out


def available(cfg: HolidaysConfig, today: date) -> list[dict[str, Any]]:
    """Every holiday we could show, on or off, for the picker in the web UI.

    Unlike :func:`upcoming` this ignores the horizon and keeps disabled entries — you
    cannot re-enable a holiday that the list has hidden from you.
    """
    own_image = {entry.name: entry.image for entry in cfg.custom}
    rows: dict[str, dict[str, Any]] = {}
    names = [(name, False) for name in calendar_names(cfg, _years(cfg, today))]
    names += [(entry.name, True) for entry in cfg.custom]
    for name, custom in names:
        if name in rows:
            continue
        override = cfg.overrides.get(name)
        explicit = own_image.get(name, "") if custom else (override.image if override else "")
        # Two different stems, and conflating them shows a broken thumbnail:
        #   image_slug — where an upload for this row would be written
        #   image_name — where the picture it shows right now actually comes from
        # They differ whenever a row borrows another's art: "Independence Day (observed)"
        # shows independence_day.png until you give the observed day one of its own.
        stem = explicit or slug(name)
        path = image_path(name, explicit)
        rows[name] = {"name": name, "display": _display(name, override), "custom": custom,
                      "enabled": override.enabled if override else True, "image": path,
                      "image_name": Path(path).stem if path else None,
                      "image_slug": stem, "uploaded": uploaded(stem)}
    return sorted(rows.values(), key=lambda r: r["display"])


KEY = "holidays"


def _today(timezone: str | None) -> date:
    try:
        return datetime.now(ZoneInfo(timezone)).date() if timezone else datetime.now().astimezone().date()
    except Exception:
        return date.today()


def _compute(cfg: HolidaysConfig, today: date) -> dict[str, list[dict[str, Any]]]:
    return {"upcoming": upcoming(cfg, today), "available": available(cfg, today)}


class HolidaysSource:
    key: ClassVar[str] = KEY
    config_model: ClassVar[type[BaseModel]] = HolidaysConfig

    async def run(self, ctx: SourceContext) -> None:
        while True:
            cfg: HolidaysConfig = ctx.config  # type: ignore[assignment]
            computed = await asyncio.to_thread(_compute, cfg, _today(ctx.timezone))
            for subkey, value in computed.items():
                # Nothing here is fetched, so most loops compute exactly what is already
                # published. Publishing anyway would bump the snapshot version and wake
                # every listener hourly for no reason.
                if ctx.snapshot().get(f"{KEY}.{subkey}") != value:
                    ctx.publish(value, subkey=subkey)
            await ctx.sleep(cfg.refresh_seconds)


def config_listener(snapshots: SnapshotStore) -> Callable[[AppConfig], None]:
    """A ``ConfigStore`` listener that republishes when the holiday settings change.

    Without it, hiding or renaming a holiday would not reach the panel until the source
    next woke, an hour later. The listener fires for *every* config change, so it compares
    the section first — the brightness slider must not trigger a calendar recompute.
    """
    previous: dict[str, Any] | None = None

    def listen(app_config: AppConfig) -> None:
        nonlocal previous
        current = app_config.sources.get(KEY, {})
        if current != previous:
            previous = current
            refresh(app_config, snapshots)

    return listen


def refresh(app_config: AppConfig, snapshots: SnapshotStore) -> None:
    """Recompute and publish right now, from whatever the config currently says.

    The source only wakes hourly, which is fine for a calendar that never changes under
    it — but not for the web UI, where uploading a picture has to show up at once. This
    is the same computation, run on demand; both writers produce the same value from the
    same config, so which one gets there first does not matter.
    """
    try:
        cfg = HolidaysConfig.model_validate(app_config.sources.get(KEY, {}))
    except ValidationError:
        log.warning("holidays: config is not valid, not refreshing")
        return
    for subkey, value in _compute(cfg, _today(app_config.location.timezone)).items():
        snapshots.publish(f"{KEY}.{subkey}", value)
