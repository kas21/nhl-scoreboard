"""Environment Canada alerts from the MSC GeoMet OGC API (keyless).

``collections/weather-alerts/items`` filtered by a bbox a few hundred metres around the
location returns one feature per forecast region polygon the alert covers. Ended alerts
stay in the collection for a while, so status is checked here.
"""
from __future__ import annotations

from typing import Any

import httpx

from .model import collapse, make_alert, summarize

ECCC_ALERTS = "https://api.weather.gc.ca/collections/weather-alerts/items"
BOX_DEGREES = 0.0005
RISK_SEVERITY = {"red": "Extreme", "orange": "Severe", "yellow": "Moderate", "grey": "Minor", "gray": "Minor"}
SENDER = "Environment Canada"


async def fetch_eccc(http: httpx.AsyncClient, lat: float, lon: float) -> dict[str, Any]:
    bbox = f"{lon - BOX_DEGREES:.4f},{lat - BOX_DEGREES:.4f},{lon + BOX_DEGREES:.4f},{lat + BOX_DEGREES:.4f}"
    resp = await http.get(ECCC_ALERTS, params={"f": "json", "lang": "en", "limit": 100, "bbox": bbox}, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"ECCC alerts: unexpected payload ({type(data).__name__})")
    return data


def parse_eccc(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Alerts still in effect. ECCC has no message chain, so the hazard code is the identity.

    Raises ValueError when the collection is not shaped like one, so the source logs a
    failed poll rather than crashing.
    """
    out = []
    features = payload.get("features") or []
    if not isinstance(features, list) or not all(isinstance(f, dict) for f in features):
        raise ValueError("ECCC alerts: features is not a list of objects")
    for feat in features:
        props = feat.get("properties") or {}
        if not isinstance(props, dict):
            raise ValueError("ECCC alerts: feature properties is not an object")
        name = props.get("alert_name_en")
        if not name or (str(props.get("status_en") or "")).lower() == "ended":
            continue
        if not isinstance(name, str):
            raise ValueError(f"ECCC alerts: alert_name_en is {type(name).__name__}, not text")
        event = name.title()
        out.append(make_alert(
            id=f"{props.get('alert_code')}:{props.get('feature_id')}:{props.get('publication_datetime')}",
            key=str(props.get("alert_code") or name).lower(), provider="eccc",
            event=event, severity=RISK_SEVERITY.get(str(props.get("risk_colour_en") or "").lower(), "Unknown"),
            headline=f"{event} in effect".upper(), summary=summarize(props.get("alert_text_en")),
            area=collapse(props.get("feature_name_en")), onset=props.get("validity_datetime"),
            expires=props.get("event_end_datetime") or props.get("expiration_datetime"), sender=SENDER,
        ))
    return out
