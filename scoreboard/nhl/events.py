"""Event detectors: diff consecutive ``main_event`` snapshots.

``detect_goals`` is the hockey rule shared with the other hockey leagues (college, AHL): the
sport key only changes which snapshot key is read and the prefix on the event kinds.
"""
from __future__ import annotations

from collections.abc import Iterable

from ..data import Event, Snapshot

MAIN_EVENT = "nhl.main_event"


def detect_goals(prev: Snapshot, new: Snapshot, sport: str = "nhl") -> Iterable[Event]:
    key = f"{sport}.main_event"
    a, b = prev.get(key), new.get(key)
    if not a or not b or a.get("id") != b.get("id"):
        return []
    ts = new.updated.get(key, 0.0)
    events: list[Event] = []
    for side in ("away", "home"):
        delta = b[side]["score"] - a[side]["score"]
        if delta > 0:
            new_goals = [g for g in b["goals"] if g["team"] == b[side]["abbrev"]][-delta:] if b.get("goals") else []
            events.append(Event(f"{sport}.goal", team=b[side]["abbrev"], ts=ts, payload={
                "side": side, "count": delta, "game": b,
                "goal": new_goals[-1] if new_goals else None,
                "score": f"{b['away']['score']}-{b['home']['score']}",
            }))
        elif delta < 0:
            events.append(Event(f"{sport}.goal_overturned", team=b[side]["abbrev"], ts=ts, payload={"side": side}))
    # By identity rather than list length: a landing that came back after a failure, or a
    # penalty the feed rescinded or re-ordered, must not replay the ones already announced.
    known = {_penalty_id(p) for p in a.get("penalties") or []}
    for pen in b.get("penalties") or []:
        if _penalty_id(pen) not in known:
            events.append(Event(f"{sport}.penalty", team=pen["team"], ts=ts, payload={"penalty": pen, "game": b}))
    if a.get("state") != b.get("state"):
        events.append(Event(f"{sport}.state_change", ts=ts, payload={"old": a.get("state"), "new": b.get("state"), "game": b}))
    if (a.get("powerplay") or {}).get("code") != (b.get("powerplay") or {}).get("code"):
        events.append(Event(f"{sport}.powerplay", ts=ts, payload={"old": (a.get("powerplay") or {}).get("code"),
                                                                 "new": (b.get("powerplay") or {}).get("code")}))
    return events


def _penalty_id(pen: dict) -> tuple:
    return tuple(pen.get(k) for k in ("team", "period", "time", "player", "desc"))


def detect_main_event(prev: Snapshot, new: Snapshot) -> Iterable[Event]:
    return detect_goals(prev, new, "nhl")
