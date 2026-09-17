"""NFL event detectors: scoring plays and state changes from consecutive ``nfl.main_event`` snapshots.

``detect_scoring`` is the football rule shared with the college plugin: the sport key only
changes which snapshot key is read and the prefix on the event kinds.
"""
from __future__ import annotations

from collections.abc import Iterable

from ..data import Event, Snapshot

MAIN_EVENT = "nfl.main_event"
# By points alone. 2 is left out on purpose: a touchdown usually lands in one poll and its
# two-point try in the next, so a bare +2 is far more often a conversion than a safety.
KINDS = {6: "touchdown", 7: "touchdown", 8: "touchdown", 3: "field_goal", 1: "extra_point"}
SKIPPED = frozenset({"extra_point", "two_point"})        # rolled into the touchdown alert


def classify(delta: int, play_type: str) -> str | None:
    """The event kind for a ``delta``-point swing, from ESPN's play type when it names one
    ("Safety", "Two Point Pass", "Field Goal Good"...), else from the points. None: no alert."""
    text = play_type.lower()
    if "safety" in text:
        kind = "safety"
    elif "two point" in text or "two-point" in text:
        kind = "two_point"
    elif "field goal" in text:
        kind = "field_goal"
    elif "extra point" in text:
        kind = "extra_point"
    elif "touchdown" in text:
        kind = "touchdown"
    elif delta == 2:
        kind = "two_point"
    else:
        kind = KINDS.get(delta, "touchdown" if delta >= 6 else "score")
    return None if kind in SKIPPED else kind


def detect_scoring(prev: Snapshot, new: Snapshot, sport: str = "nfl") -> Iterable[Event]:
    key = f"{sport}.main_event"
    a, b = prev.get(key), new.get(key)
    if not a or not b or a.get("id") != b.get("id"):
        return []
    ts = new.updated.get(key, 0.0)
    out: list[Event] = []
    for side in ("away", "home"):
        delta = b[side]["score"] - a[side]["score"]
        if delta > 0:
            kind = classify(delta, (b.get("situation") or {}).get("last_play_type") or "")
            if kind is None:
                continue
            out.append(Event(f"{sport}.{kind}", team=b[side]["abbrev"], ts=ts, payload={
                "side": side, "points": delta, "game": b, "score": f"{b['away']['score']}-{b['home']['score']}",
                "last_play": b.get("situation", {}).get("last_play", "")}))
    if a.get("state") != b.get("state"):
        out.append(Event(f"{sport}.state_change", ts=ts, payload={"old": a.get("state"), "new": b.get("state"), "game": b}))
    return out


def detect_nfl(prev: Snapshot, new: Snapshot) -> Iterable[Event]:
    return detect_scoring(prev, new, "nfl")
