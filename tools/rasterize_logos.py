"""Pre-rasterise SVG logos to PNG at the sizes the size profiles use.

Run at build time (``uv run --extra build python tools/rasterize_logos.py``) so
cairosvg is never needed on the Pi. Output: scoreboard/assets/logos/png/{TEAM}_{px}.png
"""
from __future__ import annotations

import sys
from pathlib import Path

import cairosvg

ROOT = Path(__file__).parent.parent / "scoreboard" / "assets" / "logos"
SIZES = (16, 24, 32, 48, 64, 96, 128)


def main() -> int:
    out = ROOT / "png"
    out.mkdir(exist_ok=True)
    svgs = sorted((ROOT / "svg").glob("*.svg"))
    for svg in svgs:
        for px in SIZES:
            target = out / f"{svg.stem}_{px}.png"
            cairosvg.svg2png(url=str(svg), write_to=str(target), output_width=px, output_height=px)
    print(f"rasterised {len(svgs)} logos x {len(SIZES)} sizes -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
