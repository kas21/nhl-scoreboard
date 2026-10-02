"""Team stats for one game, from Game Center's right rail.

``/gamecenter/{id}/right-rail`` carries ``teamGameStats`` (shots, faceoff wins and percentage,
power play, PIM, hits, blocked shots, giveaways, takeaways), ``shotsByPeriod`` and a
``linescore``. It is what the Game Center sidebar shows, live and after. The NHL publishes no
possession figure; faceoffs are the nearest thing the feed has.
"""
from __future__ import annotations

from typing import Any

# (feed category, long label, short label for narrow panels)
CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("sog", "SHOTS", "SHOTS"),
    ("faceoffWinningPctg", "FACEOFFS", "FO%"),
    ("hits", "HITS", "HITS"),
    ("blockedShots", "BLOCKED", "BLK"),
    ("pim", "PIM", "PIM"),
    ("powerPlay", "POWER PLAY", "PP"),
    ("giveaways", "GIVEAWAYS", "GVA"),
    ("takeaways", "TAKEAWAYS", "TKA"),
)
PERIOD_NAMES = {1: "1ST", 2: "2ND", 3: "3RD"}


def normalize_game_stats(rail: dict[str, Any], game_id: int | None, away: str, home: str) -> dict[str, Any]:
    """The ``nhl.game_stats`` value: one row per stat with both values, their display text and
    the away side's share of the whole (0.5 when there is nothing to compare yet), then shots
    and goals by period as rows of the same shape."""
    raw = {row.get("category"): row for row in rail.get("teamGameStats") or [] if isinstance(row, dict)}
    rows: list[dict[str, Any]] = []
    for key, label, short in CATEGORIES:
        row = raw.get(key)
        if row is None:
            continue
        a, h = row.get("awayValue"), row.get("homeValue")
        if key == "faceoffWinningPctg":
            wins = raw.get("faceoffWins") or {}
            rows.append(_row(key, label, short, _pct_text(a), _pct_text(h), _share(_num(a), _num(h)),
                             detail=(wins.get("awayValue"), wins.get("homeValue"))))
        elif key == "powerPlay":
            pct = raw.get("powerPlayPctg") or {}
            ag, hg = _fraction(a), _fraction(h)
            rows.append(_row(key, label, short, str(a or "0/0"), str(h or "0/0"), _share(ag[0], hg[0]),
                             detail=(_pct_text(pct.get("awayValue")), _pct_text(pct.get("homeValue")))))
        else:
            rows.append(_row(key, label, short, _count_text(a), _count_text(h), _share(_num(a), _num(h))))
    periods: list[dict[str, Any]] = []
    for p in rail.get("shotsByPeriod") or []:
        name = _period_name(p.get("periodDescriptor") or {})
        periods.append(_row(f"sog_p{(p.get('periodDescriptor') or {}).get('number', 0)}", f"SHOTS {name}", f"SOG {name}",
                            _count_text(p.get("away")), _count_text(p.get("home")), _share(_num(p.get("away")), _num(p.get("home")))))
    goals: list[dict[str, Any]] = []
    for p in (rail.get("linescore") or {}).get("byPeriod") or []:
        name = _period_name(p.get("periodDescriptor") or {})
        goals.append(_row(f"goals_p{(p.get('periodDescriptor') or {}).get('number', 0)}", f"GOALS {name}", f"G {name}",
                          _count_text(p.get("away")), _count_text(p.get("home")), _share(_num(p.get("away")), _num(p.get("home")))))
    totals = (rail.get("linescore") or {}).get("totals") or {}
    return {
        "game_id": game_id,
        "away": {"abbrev": away, "score": int(_num(totals.get("away")) or 0)},
        "home": {"abbrev": home, "score": int(_num(totals.get("home")) or 0)},
        "stats": rows,
        "shots_by_period": periods,
        "goals_by_period": goals,
    }


def _row(key: str, label: str, short: str, away_text: str, home_text: str, share: float,
         detail: tuple[Any, Any] | None = None) -> dict[str, Any]:
    row = {"key": key, "label": label, "short": short, "away": away_text, "home": home_text, "share": round(share, 4)}
    if detail is not None:
        row["detail"] = [detail[0], detail[1]]
    return row


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _count_text(value: Any) -> str:
    n = _num(value)
    return "0" if n is None else str(int(n)) if n == int(n) else f"{n:g}"


def _pct_text(value: Any) -> str:
    n = _num(value)
    return "-" if n is None else f"{int(round(n * 100))}%"


def _fraction(value: Any) -> tuple[float, float]:
    """``"1/3"`` -> (1, 3); anything else -> (0, 0)."""
    try:
        made, tried = str(value).split("/")
        return float(made), float(tried)
    except (AttributeError, ValueError):
        return 0.0, 0.0


def _share(a: float | None, h: float | None) -> float:
    """The away side's share of the two values: half when there is nothing to compare."""
    a, h = a or 0.0, h or 0.0
    if a <= 0 and h <= 0:
        return 0.5
    return max(min(a / (a + h), 1.0), 0.0)


def _period_name(descriptor: dict[str, Any]) -> str:
    number = int(descriptor.get("number") or 0)
    ptype = descriptor.get("periodType", "REG")
    if ptype == "SO":
        return "SO"
    if ptype == "OT":
        ot_n = number - int(descriptor.get("maxRegulationPeriods") or 3)
        return f"{ot_n}OT" if ot_n > 1 else "OT"
    return PERIOD_NAMES.get(number, f"{number}TH")


def stats_candidate(snapshot: Any, favorites: list[str]) -> dict[str, Any] | None:
    """The game whose stats to show: the main event while it is on or over, else the favourite's
    most recent result from its team summary (last night's numbers, the morning after; the
    right rail of a finished game stays up). Not the Simulator's game (no right rail behind
    it), not a game that has not started (nothing to show), and nothing until the scores loop
    has published at all."""
    if not snapshot.has("nhl.main_event"):
        return None
    main = snapshot.get("nhl.main_event") or {}
    if main.get("simulated"):
        return None
    if main.get("id") and main.get("phase") in ("live", "intermission", "postgame"):
        return {"id": main["id"], "phase": main["phase"], "away": main["away"]["abbrev"], "home": main["home"]["abbrev"]}
    summaries = snapshot.get("nhl.team_summary") or {}
    for fav in favorites:
        prev = (summaries.get(fav) or {}).get("prev_game") or {}
        if prev.get("id") and prev.get("result"):
            us, them = fav, prev.get("opponent", "")
            away, home = (them, us) if prev.get("home") else (us, them)
            return {"id": prev["id"], "phase": "postgame", "away": away, "home": home}
    return None
