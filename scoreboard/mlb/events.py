"""MLB event detectors: runs (home runs when the live feed says so) and state changes from
consecutive ``mlb.main_event`` snapshots."""
from __future__ import annotations

from collections.abc import Iterable

from ..data import Event, Snapshot

MAIN_EVENT = "mlb.main_event"


def detect_mlb(prev: Snapshot, new: Snapshot) -> Iterable[Event]:
    a, b = prev.get(MAIN_EVENT), new.get(MAIN_EVENT)
    if not a or not b or a.get("id") != b.get("id"):
        return []
    ts = new.updated.get(MAIN_EVENT, 0.0)
    out: list[Event] = []
    sit = b.get("situation") or {}
    last = sit.get("last_play") or {}
    for side in ("away", "home"):
        delta = b[side]["score"] - a[side]["score"]
        if delta <= 0:
            continue
        homer = last.get("type") == "home_run" and last.get("batting", side) == side
        out.append(Event("mlb.home_run" if homer else "mlb.run", team=b[side]["abbrev"], ts=ts, payload={
            "side": side, "runs": delta, "game": b, "score": f"{b['away']['score']}-{b['home']['score']}",
            "inning": sit.get("inning_ordinal", ""), "half": sit.get("half", ""),
            "batter": sit.get("batter", ""), "text": last.get("text", "") if homer else ""}))
    if a.get("state") != b.get("state"):
        out.append(Event("mlb.state_change", ts=ts, payload={"old": a.get("state"), "new": b.get("state"), "game": b}))
    sa, sb = a.get("situation") or {}, sit
    if (sa.get("inning"), sa.get("half")) != (sb.get("inning"), sb.get("half")) and b.get("phase") == "live":
        out.append(Event("mlb.inning_change", ts=ts, payload={"inning": sb.get("inning"), "half": sb.get("half"), "game": b}))
    return out
