"""Holiday countdown data: upcoming public holidays (``holidays`` package) plus custom dates."""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from ...config.models import ADVANCED
from ...data.source import SourceContext
from ...imagecache import DATA_ROOT

log = logging.getLogger(__name__)

IMAGES = Path(__file__).parent.parent.parent / "assets" / "holidays"
# Pictures the user uploaded. Kept outside the checkout so an OTA update, which
# fast-forwards the working tree, cannot delete them.
USER_IMAGES = DATA_ROOT / "holidays"

# The library defaults to PUBLIC only, which is far narrower than what a scoreboard
# wants to count down to: it omits Halloween, Groundhog Day and Mother's / Father's Day
# in the US (all of which we ship art for), and Victoria Day and the National Day for
# Truth and Reconciliation in Canada, which are federally GOVERNMENT days. Categories a
# country does not support are dropped rather than raising.
CATEGORIES = ("public", "unofficial", "optional", "government")

# A slug is used as a filename, so it may only ever be [a-z0-9_].
SLUG_PATTERN = r"^[a-z0-9_]*$"
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*$")


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
    model_config = ConfigDict(frozen=True, extra="forbid", title="Holidays")
    country: str = Field("US", min_length=2, max_length=2, description="ISO country code, e.g. US, CA, GB")
    subdivision: str = Field("", max_length=5, description="State / province code (optional, e.g. NY, ON)")
    horizon_days: int = Field(90, ge=1, le=365, description="How far ahead to look")
    custom: list[CustomHoliday] = Field([], description="Your own dates (birthdays, puck drop, ...)")
    overrides: dict[str, HolidayOverride] = Field(
        {}, description="Per-holiday changes, keyed by the official name: hide it, rename it, repicture it"
    )
    refresh_seconds: int = Field(3600, ge=300, le=86400, json_schema_extra=ADVANCED)


def slug(name: str) -> str:
    """Filename stem for a holiday name.

    Apostrophes are dropped rather than treated as separators: they used to split the
    word, so ``New Year's Day`` looked for ``new_year_s_day.png`` and never found the
    ``new_years_day.png`` we ship. Accents fold to their base letter for the same
    reason, so a French calendar does not ask for ``f_te_du_canada.png``.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", folded.lower().replace("'", "")).strip("_")


def base_name(name: str) -> str:
    """``Independence Day (observed)`` -> ``Independence Day``, so it borrows the same picture."""
    return _PARENTHETICAL.sub("", name).strip() or name


def image_path(name: str, explicit: str = "") -> str | None:
    """The picture for a holiday, as an absolute path, or None if we have none.

    An explicit slug wins, then the holiday's own name, then the name with any
    ``(observed)`` suffix removed. At each step an uploaded picture beats the bundled
    one, so replacing the shipped artwork is a matter of dropping a file in.
    """
    for stem in dict.fromkeys(s for s in (explicit, slug(name), slug(base_name(name))) if s):
        for root in (USER_IMAGES, IMAGES):
            path = root / f"{stem}.png"
            if path.exists():
                return str(path)
    return None


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
    rows: dict[str, dict[str, Any]] = {}
    names = [(name, False) for name in calendar_names(cfg, _years(cfg, today))]
    names += [(entry.name, True) for entry in cfg.custom]
    for name, custom in names:
        if name in rows:
            continue
        override = cfg.overrides.get(name)
        explicit = next((e.image for e in cfg.custom if e.name == name and e.image), "") if custom else \
            (override.image if override else "")
        rows[name] = {"name": name, "display": _display(name, override), "custom": custom,
                      "enabled": override.enabled if override else True, "image": image_path(name, explicit)}
    return sorted(rows.values(), key=lambda r: r["display"])


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
            counted, everything = await asyncio.to_thread(_compute, cfg, today)
            ctx.publish(counted, subkey="upcoming")
            ctx.publish(everything, subkey="available")
            await ctx.sleep(cfg.refresh_seconds)


def _compute(cfg: HolidaysConfig, today: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return upcoming(cfg, today), available(cfg, today)
