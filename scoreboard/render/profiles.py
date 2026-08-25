"""Size profiles: per-panel-size layout parameters.

Boards read sizes from the profile instead of hardcoding pixels, so one board
works from 64x32 up to 256x256. Unknown sizes pick the nearest smaller profile.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizeProfile:
    name: str
    width: int
    height: int
    font_small: int
    font_medium: int
    font_large: int
    font_score: int
    logo: int          # square logo edge in px (hero layouts)
    logo_small: int    # logo edge next to scores
    pad: int           # general padding
    show_sog: bool = True
    show_records: bool = True


PROFILES: tuple[SizeProfile, ...] = (
    SizeProfile("64x32", 64, 32, font_small=6, font_medium=8, font_large=10, font_score=14, logo=14, logo_small=10, pad=1, show_sog=False, show_records=False),
    SizeProfile("64x64", 64, 64, font_small=6, font_medium=8, font_large=12, font_score=18, logo=22, logo_small=14, pad=2, show_records=False),
    SizeProfile("128x32", 128, 32, font_small=6, font_medium=8, font_large=12, font_score=16, logo=20, logo_small=14, pad=2, show_sog=False),
    SizeProfile("128x64", 128, 64, font_small=8, font_medium=10, font_large=16, font_score=26, logo=36, logo_small=22, pad=2),
    SizeProfile("128x128", 128, 128, font_small=8, font_medium=12, font_large=20, font_score=40, logo=56, logo_small=40, pad=3),
    SizeProfile("192x128", 192, 128, font_small=10, font_medium=14, font_large=24, font_score=48, logo=64, logo_small=48, pad=4),
    SizeProfile("256x256", 256, 256, font_small=12, font_medium=18, font_large=32, font_score=72, logo=110, logo_small=80, pad=6),
)


def profile_for(width: int, height: int) -> SizeProfile:
    exact = next((p for p in PROFILES if (p.width, p.height) == (width, height)), None)
    if exact:
        return exact
    target = width * height
    smaller = [p for p in PROFILES if p.width * p.height <= target and p.width <= width and p.height <= height]
    if smaller:
        return max(smaller, key=lambda p: p.width * p.height)
    return min(PROFILES, key=lambda p: p.width * p.height)
