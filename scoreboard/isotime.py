"""ISO-8601 timestamps as the APIs send them: ``Z`` suffixes, fractional seconds, or naive."""
from __future__ import annotations

from datetime import UTC, datetime


def parse_iso(iso: str | None) -> datetime | None:
    """An aware datetime from an ISO string, or None when there is nothing to parse.

    ``Z`` is accepted (Python accepts it from 3.11, but the replace costs nothing and
    documents the intent); a naive timestamp is taken as UTC, which is what every feed
    here means by one.
    """
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
