"""Which artwork a team uses, and the clubs whose default logo fails on an LED panel.

ESPN publishes a dozen branded variants per team — the primary and secondary marks,
each in several colour treatments — but only behind a per-team GUID that its team API
hands out. The flat ``/i/teamlogos/`` path :mod:`scoreboard.logos` fetches by default is
the primary mark in club colours, which fails two ways once it is scaled to a 22px tile
on a black panel:

* **wordmark primaries** turn to mush at any size (Washington, Los Angeles) — the fix is
  the *secondary* mark, which is a symbol rather than lettering;
* **dark-on-dark marks** all but vanish (Tampa Bay's navy bolt, Toronto's navy leaf) —
  the fix is the same mark in its light treatment.

:data:`CURATED` is the audited result for the NHL: only teams where the default genuinely
fails, so the board looks right out of the box. Config overrides win over it either way.
"""
from __future__ import annotations

DEFAULT_VARIANT = "default"

# Friendly name -> the ``rel`` ESPN tags the variant with in its team API.
# ``default``/``dark`` are the flat /i/teamlogos/ paths; the rest live under the GUID path.
VARIANTS: dict[str, str] = {
    DEFAULT_VARIANT: "full/default",
    "dark": "full/dark",
    "primary_on_black": "full/primary_logo_on_black_color",
    "primary_white": "full/primary_logo_white",
    "primary_on_primary": "full/primary_logo_on_primary_color",
    "secondary_on_black": "full/secondary_logo_on_black_color",
    "secondary_white": "full/secondary_logo_white",
    "secondary_on_primary": "full/secondary_logo_on_primary_color",
}
FLAT_VARIANTS = frozenset({DEFAULT_VARIANT, "dark"})

# Audited at 22px (the 128x64 `logo_small` tile) against a black panel, 2026-08.
CURATED: dict[str, dict[str, str]] = {
    "nhl": {
        "COL": "secondary_on_black",     # the "A" mountain muddies; the burgundy C stays crisp
        "LAK": "secondary_on_black",     # primary is a KINGS wordmark
        "TBL": "primary_on_black",       # navy bolt on black is all but invisible
        "TOR": "primary_on_black",       # navy leaf on black, same problem
        "VAN": "primary_on_black",       # navy orca on black, same problem
        "WSH": "secondary_on_black",     # primary is a "capitals" wordmark; secondary is the Weagle
    },
}


def is_variant(name: str) -> bool:
    return name in VARIANTS


def curated(sport: str, abbrev: str) -> str | None:
    return CURATED.get(sport, {}).get(abbrev.upper())
