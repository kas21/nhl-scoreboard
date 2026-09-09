"""One alert shape whichever agency issued it, and the rules that pick which ones to show.

    {id, key, provider, event, name, level, severity, urgency, headline, summary,
     area, onset, expires, sender}

``level`` is what the panel colours by (warning / watch / advisory / statement / other),
read off the event name; ``key`` is the dedupe identity. An agency re-issues the same
warning under a new id every update and the NWS trims the county list as a storm moves
on, so the only thing that holds still for the life of a hazard is its name — one card
and one interrupt per kind of alert in force.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

LEVELS = ("warning", "watch", "advisory", "statement", "other")
# "other" is an alert whose name ends in none of the four (Air Quality Alert, Civil Emergency
# Message, Evacuation Immediate): kept and sorted with the advisories rather than filed
# below statements, where the default minimum would silently drop it.
LEVEL_RANK = {"warning": 0, "watch": 1, "advisory": 2, "other": 2, "statement": 3}
SEVERITY_RANK = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3, "Unknown": 4}
SUMMARY_CHARS = 200
FAR_FUTURE = datetime.max.replace(tzinfo=UTC)


class OutOfBounds(ValueError):
    """The provider does not cover the configured location."""


class Selection(Protocol):
    ignore: Sequence[str]
    min_level: str


def classify_event(event: str) -> tuple[str, str]:
    """('warning', 'Tornado') for 'Tornado Warning'; ('other', name) when the last word is not a level."""
    words = event.split()
    if len(words) > 1 and words[-1].lower() in LEVELS[:4]:
        return words[-1].lower(), " ".join(words[:-1])
    return "other", event


def collapse(text: str | None) -> str:
    return " ".join((text or "").split())


def summarize(text: str | None, limit: int = SUMMARY_CHARS) -> str:
    """The start of a description, whitespace collapsed, cut at a word so it fits ``limit``."""
    flat = collapse(text)
    if len(flat) <= limit:
        return flat
    cut = flat[: limit - 3].rsplit(" ", 1)[0]
    return f"{cut}..."


def parse_when(iso: str | None) -> datetime | None:
    """An aware datetime from an ISO string (``Z`` accepted; naive means UTC), or None."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def make_alert(*, id: str, provider: str, event: str, severity: str, headline: str, summary: str, area: str,
               onset: str | None, expires: str | None, sender: str, urgency: str | None = None) -> dict[str, Any]:
    level, name = classify_event(event)
    return {
        "id": id, "key": event.lower(), "provider": provider,
        "event": event, "name": name, "level": level, "severity": severity, "urgency": urgency,
        "headline": headline, "summary": summary, "area": area,
        "onset": onset, "expires": expires, "sender": sender,
    }


def in_force(alert: dict[str, Any], now: datetime) -> bool:
    expires = parse_when(alert.get("expires"))
    return expires is None or expires > now


def _sort_key(alert: dict[str, Any]) -> tuple:
    return (LEVEL_RANK.get(alert.get("level"), len(LEVELS)), SEVERITY_RANK.get(alert.get("severity"), 4),
            parse_when(alert.get("expires")) or FAR_FUTURE, alert.get("event", ""))


def select_alerts(alerts: Iterable[dict[str, Any]], cfg: Selection, now: datetime) -> list[dict[str, Any]]:
    """What the panel shows: in force, not ignored, serious enough; most serious first, the
    soonest to lapse ahead of its peers; one per kind of alert."""
    ignore = [w.strip().lower() for w in cfg.ignore if w.strip()]
    max_rank = LEVEL_RANK[cfg.min_level]
    kept = [a for a in alerts
            if in_force(a, now) and LEVEL_RANK.get(a.get("level"), len(LEVELS)) <= max_rank
            and not any(w in a.get("event", "").lower() for w in ignore)]
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for alert in sorted(kept, key=_sort_key):
        if alert["key"] not in seen:
            seen.add(alert["key"])
            out.append(alert)
    return out
