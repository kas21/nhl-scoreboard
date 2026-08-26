"""NFL event detectors: scoring plays and state changes from consecutive ``nfl.main_event`` snapshots."""
from __future__ import annotations

from collections.abc import Iterable

from ..data import Event, Snapshot

MAIN_EVENT = "nfl.main_event"
KINDS = {6: "touchdown", 7: "touchdown", 8: "touchdown", 3: "field_goal", 2: "safety", 1: "extra_point"}


def detect_nfl(prev: Snapshot, new: Snapshot) -> Iterable[Event]:
    a, b = prev.get(MAIN_EVENT), new.get(MAIN_EVENT)
    if not a or not b or a.get("id") != b.get("id"):
        return []
    ts = new.updated.get(MAIN_EVENT, 0.0)
    out: list[Event] = []
    for side in ("away", "home"):
        delta = b[side]["score"] - a[side]["score"]
        if delta > 0:
            kind = KINDS.get(delta, "touchdown" if delta >= 6 else "score")
            if kind == "extra_point":
                continue                         # rolled into the touchdown alert
            out.append(Event(f"nfl.{kind}", team=b[side]["abbrev"], ts=ts, payload={
                "side": side, "points": delta, "game": b, "score": f"{b['away']['score']}-{b['home']['score']}",
                "last_play": b.get("situation", {}).get("last_play", "")}))
    if a.get("state") != b.get("state"):
        out.append(Event("nfl.state_change", ts=ts, payload={"old": a.get("state"), "new": b.get("state"), "game": b}))
    return out
