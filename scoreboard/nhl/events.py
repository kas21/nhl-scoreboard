"""Event detectors: diff consecutive ``main_event`` snapshots."""
from __future__ import annotations

from collections.abc import Iterable

from ..data import Event, Snapshot

MAIN_EVENT = "nhl.main_event"


def detect_main_event(prev: Snapshot, new: Snapshot) -> Iterable[Event]:
    a, b = prev.get(MAIN_EVENT), new.get(MAIN_EVENT)
    if not a or not b or a.get("id") != b.get("id"):
        return []
    ts = new.updated.get(MAIN_EVENT, 0.0)
    events: list[Event] = []
    for side in ("away", "home"):
        delta = b[side]["score"] - a[side]["score"]
        if delta > 0:
            new_goals = [g for g in b["goals"] if g["team"] == b[side]["abbrev"]][-delta:] if b.get("goals") else []
            events.append(Event("nhl.goal", team=b[side]["abbrev"], ts=ts, payload={
                "side": side, "count": delta, "game": b,
                "goal": new_goals[-1] if new_goals else None,
                "score": f"{b['away']['score']}-{b['home']['score']}",
            }))
        elif delta < 0:
            events.append(Event("nhl.goal_overturned", team=b[side]["abbrev"], ts=ts, payload={"side": side}))
    if len(b.get("penalties", [])) > len(a.get("penalties", [])):
        for pen in b["penalties"][len(a.get("penalties", [])):]:
            events.append(Event("nhl.penalty", team=pen["team"], ts=ts, payload={"penalty": pen, "game": b}))
    if a.get("state") != b.get("state"):
        events.append(Event("nhl.state_change", ts=ts, payload={"old": a.get("state"), "new": b.get("state"), "game": b}))
    if a["powerplay"]["code"] != b["powerplay"]["code"]:
        events.append(Event("nhl.powerplay", ts=ts, payload={"old": a["powerplay"]["code"], "new": b["powerplay"]["code"]}))
    return events
