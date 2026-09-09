"""National Weather Service alerts (api.weather.gov, keyless, US only).

``alerts/active?point=lat,lon`` returns the CAP alerts whose zones cover the point —
county-level, so a warning for the far end of the county shows too. Coordinates take at
most four decimals; a point outside the US is a 400 "out of bounds".
"""
from __future__ import annotations

from typing import Any

import httpx

from .model import OutOfBounds, collapse, make_alert, summarize

NWS_ALERTS = "https://api.weather.gov/alerts/active?point={lat:.4f},{lon:.4f}"
HEADERS = {"Accept": "application/geo+json"}


async def fetch_nws(http: httpx.AsyncClient, lat: float, lon: float) -> dict[str, Any]:
    resp = await http.get(NWS_ALERTS.format(lat=lat, lon=lon), headers=HEADERS, follow_redirects=True)
    if resp.status_code == 400 and "out of bounds" in resp.text.lower():
        raise OutOfBounds(f"NWS does not cover {lat:.4f},{lon:.4f}")
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else {}


def _headline(props: dict[str, Any], event: str) -> str:
    """NWS's own all-caps one-liner when there is one, else the long headline."""
    short = ((props.get("parameters") or {}).get("NWSheadline") or [None])[0]
    return collapse(short) if short else collapse(props.get("headline")) or event


def parse_nws(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Real, current alerts from a GeoJSON FeatureCollection; tests, exercises and cancellations dropped."""
    out = []
    for feat in payload.get("features") or []:
        props = feat.get("properties") or {}
        event = props.get("event")
        if not event or props.get("status") != "Actual" or props.get("messageType") == "Cancel":
            continue
        out.append(make_alert(
            id=props.get("id") or feat.get("id") or event, provider="nws", event=event,
            severity=props.get("severity") or "Unknown", urgency=props.get("urgency"),
            headline=_headline(props, event), summary=summarize(props.get("description") or props.get("headline")),
            area=collapse(props.get("areaDesc")), onset=props.get("onset") or props.get("effective"),
            expires=props.get("ends") or props.get("expires"),          # `expires` is the message; `ends` the event
            sender=props.get("senderName") or "",
        ))
    return out
