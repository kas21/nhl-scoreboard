"""Tile the golden frames into one labelled contact sheet, zoomed so LED-sized panels are readable.

    uv run python tools/golden_sheet.py                 # -> tests/golden/_failed/sheet.png (gitignored)
    uv run python tools/golden_sheet.py out.png 3       # custom path, 3x zoom

The frames are the checked-in goldens, so this is the board gallery: one glance shows what
every board looks like at every profile we pin.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

GOLDEN_DIR = Path(__file__).parent.parent / "tests" / "golden"
DEFAULT_OUT = GOLDEN_DIR / "_failed" / "sheet.png"
LABEL_H = 12
PAD = 6
BACKGROUND = (24, 24, 24)
LABEL = (180, 180, 180)


def frames() -> list[tuple[str, Image.Image]]:
    paths = sorted(p for p in GOLDEN_DIR.rglob("*.png") if "_failed" not in p.parts)
    return [(f"{p.parent.name}/{p.stem}", Image.open(p).convert("RGB")) for p in paths]


def build(zoom: int) -> Image.Image:
    tiles = frames()
    if not tiles:
        raise SystemExit(f"no goldens under {GOLDEN_DIR}; run SCOREBOARD_UPDATE_GOLDENS=1 uv run pytest tests/test_golden.py")
    cell_w = max(img.width for _, img in tiles) * zoom + PAD
    cell_h = max(img.height for _, img in tiles) * zoom + LABEL_H + PAD
    columns = max(1, 1600 // cell_w)
    rows = -(-len(tiles) // columns)
    sheet = Image.new("RGB", (columns * cell_w + PAD, rows * cell_h + PAD), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    for i, (label, img) in enumerate(tiles):
        x, y = PAD + (i % columns) * cell_w, PAD + (i // columns) * cell_h
        draw.text((x, y), label, fill=LABEL)
        big = img.resize((img.width * zoom, img.height * zoom), Image.NEAREST)
        sheet.paste(big, (x, y + LABEL_H))
    return sheet


def main(argv: list[str]) -> None:
    out = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    zoom = int(argv[2]) if len(argv) > 2 else 2
    out.parent.mkdir(parents=True, exist_ok=True)
    build(zoom).save(out)
    print(out)


if __name__ == "__main__":
    main(sys.argv)
