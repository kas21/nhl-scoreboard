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
    if not isinstance(data, dict):
        raise ValueError(f"NWS alerts: unexpected payload ({type(data).__name__})")
    return data


def _headline(props: dict[str, Any], event: str) -> str:
    """NWS's own all-caps one-liner when there is one, else the long headline."""
    short = ((props.get("parameters") or {}).get("NWSheadline") or [None])[0]
    return collapse(short) if short else collapse(props.get("headline")) or event


def _references(props: dict[str, Any]) -> list[str]:
    """Ids of the messages this one supersedes, earliest sent first (the chain's root leads)."""
    refs = [r for r in (props.get("references") or []) if isinstance(r, dict) and r.get("identifier")]
    return [r["identifier"] for r in sorted(refs, key=lambda r: r.get("sent") or "")]


def parse_nws(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Real, current alerts from a GeoJSON FeatureCollection; tests, exercises and cancellations dropped.

    Raises ValueError when the collection is not shaped like one, so the source logs a
    failed poll rather than crashing.
    """
    out = []
    features = payload.get("features") or []
    if not isinstance(features, list) or not all(isinstance(f, dict) for f in features):
        raise ValueError("NWS alerts: features is not a list of objects")
    for feat in features:
        props = feat.get("properties") or {}
        if not isinstance(props, dict):
            raise ValueError("NWS alerts: feature properties is not an object")
        event = props.get("event")
        if not event or props.get("status") != "Actual" or props.get("messageType") == "Cancel":
            continue
        if not isinstance(event, str):
            raise ValueError(f"NWS alerts: event is {type(event).__name__}, not text")
        msg_id = props.get("id") or feat.get("id") or event
        out.append(make_alert(
            id=msg_id, key=msg_id, references=_references(props), provider="nws", event=event,
            severity=props.get("severity") or "Unknown", urgency=props.get("urgency"),
            headline=_headline(props, event), summary=summarize(props.get("description") or props.get("headline")),
            area=collapse(props.get("areaDesc")), onset=props.get("onset") or props.get("effective"),
            expires=props.get("ends") or props.get("expires"),          # `expires` is the message; `ends` the event
            sender=props.get("senderName") or "",
        ))
    return out
