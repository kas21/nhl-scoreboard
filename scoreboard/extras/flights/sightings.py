"""How many times each airframe has been seen.

The poll only ever says what is in range *now*; this remembers it. One flyover spans
several polls, so a visit is counted once: an airframe seen again within ``gap_seconds``
of its last poll is the same visit, later than that is a new one.

Keyed by ICAO hex (every aircraft has one, and it names the airframe); the registration
is what people recognise, so it is kept for display. Persisted as one JSON file under the
data directory, next to the uploaded holiday pictures, so an update cannot wipe it.
Writes are atomic and debounced; a file that will not parse is set aside, never trusted.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

VISIT_GAP_SECONDS = 30 * 60
MAX_AIRFRAMES = 5000
DAILY_KEEP = 60
SAVE_INTERVAL_SECONDS = 60.0
TOP_N = 5
REGULAR_FIELDS = ("hex", "registration", "type", "operator", "count", "last_seen")


def _entry_from(ac: dict[str, Any], prev: dict[str, Any] | None, now: float, new_visit: bool) -> dict[str, Any]:
    prev = prev or {}
    return {
        "registration": ac.get("registration") or prev.get("registration", ""),
        "type": ac.get("type") or prev.get("type", ""),
        "operator": ac.get("operator") or ac.get("airline") or prev.get("operator", ""),
        "count": int(prev.get("count", 0)) + (1 if new_visit else 0),
        "first_seen": prev.get("first_seen", now),
        "last_seen": now,
    }


def _valid_entry(e: Any) -> bool:
    return isinstance(e, dict) and isinstance(e.get("count"), int) and isinstance(e.get("last_seen"), (int, float))


class SightingLog:
    def __init__(self, path: Path | str, max_airframes: int = MAX_AIRFRAMES, save_interval: float = SAVE_INTERVAL_SECONDS) -> None:
        self._path = Path(path)
        self._max = max_airframes
        self._save_interval = save_interval
        self._airframes: dict[str, dict[str, Any]] = {}
        self._daily: dict[str, int] = {}
        self._loaded = False
        self._dirty = False
        self._last_save = 0.0

    # -- persistence ------------------------------------------------------------

    def load(self) -> None:
        self._loaded = True
        if not self._path.exists():
            return
        try:
            doc = json.loads(self._path.read_text())
            airframes, daily = doc.get("airframes"), doc.get("daily")
            if not isinstance(airframes, dict) or not isinstance(daily, dict):
                raise ValueError("unexpected shape")
            self._airframes = {h: e for h, e in airframes.items() if isinstance(h, str) and _valid_entry(e)}
            self._daily = {d: int(n) for d, n in daily.items() if isinstance(d, str)}
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            broken = self._path.with_suffix(".json.broken")
            log.error("sightings log %s is unusable (%s); moved to %s, starting empty", self._path, exc, broken)
            try:
                os.replace(self._path, broken)
            except OSError:
                pass
            self._airframes, self._daily = {}, {}

    def flush(self) -> None:
        """Write now if anything changed (call on shutdown)."""
        if not self._dirty:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"version": 1, "airframes": self._airframes, "daily": self._daily}) + "\n")
            os.replace(tmp, self._path)
            self._dirty = False
        except OSError as exc:
            log.warning("could not save sightings log %s: %s", self._path, exc)

    def _maybe_save(self, now: float) -> None:
        if self._dirty and now - self._last_save >= self._save_interval:
            self.flush()
            self._last_save = now

    # -- recording ---------------------------------------------------------------

    def record(self, aircraft: list[dict[str, Any]], now: float, today: str, gap_seconds: float = VISIT_GAP_SECONDS) -> list[dict[str, Any]]:
        """Note every aircraft in ``aircraft``; returns them with ``sightings`` and ``first_seen`` added."""
        if not self._loaded:
            self.load()
        airframes = dict(self._airframes)
        out, new_visits = [], 0
        for ac in aircraft:
            hex_ = ac.get("hex")
            if not hex_:
                out.append(ac)
                continue
            prev = airframes.get(hex_)
            new_visit = prev is None or now - prev["last_seen"] > gap_seconds
            entry = _entry_from(ac, prev, now, new_visit)
            airframes[hex_] = entry
            new_visits += new_visit
            out.append({**ac, "sightings": entry["count"], "first_seen": entry["first_seen"]})
        self._airframes = _trimmed(airframes, self._max)
        if new_visits:
            self._daily = _recent_days({**self._daily, today: self._daily.get(today, 0) + new_visits})
        self._dirty = self._dirty or bool(aircraft)
        self._maybe_save(now)
        return out

    def stats(self, today: str) -> dict[str, Any]:
        if not self._loaded:
            self.load()
        ranked = sorted(self._airframes.items(), key=lambda kv: (-kv[1]["count"], -kv[1]["last_seen"]))
        return {
            "airframes": len(self._airframes),
            "sightings": sum(e["count"] for e in self._airframes.values()),
            "today": self._daily.get(today, 0),
            "since": min((e["first_seen"] for e in self._airframes.values()), default=None),
            "regulars": [{k: v for k, v in {"hex": h, **e}.items() if k in REGULAR_FIELDS} for h, e in ranked[:TOP_N]],
        }


def _trimmed(airframes: dict[str, dict[str, Any]], limit: int) -> dict[str, dict[str, Any]]:
    """Drop the least interesting airframes (fewest visits, longest ago) once past ``limit``."""
    if len(airframes) <= limit:
        return airframes
    keep = sorted(airframes.items(), key=lambda kv: (-kv[1]["count"], -kv[1]["last_seen"]))[:limit]
    return dict(keep)


def _recent_days(daily: dict[str, int]) -> dict[str, int]:
    return dict(sorted(daily.items())[-DAILY_KEEP:])
