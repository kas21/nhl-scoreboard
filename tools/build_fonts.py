"""Convert bundled BDF bitmap fonts to Pillow .pil/.pbm (build step, committed)."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import BdfFontFile

FONTS = Path(__file__).parent.parent / "scoreboard" / "render" / "fonts"


def main() -> int:
    out = FONTS / "pil"
    out.mkdir(exist_ok=True)
    for bdf in sorted((FONTS / "bdf").glob("*.bdf")):
        with bdf.open("rb") as fh:
            BdfFontFile.BdfFontFile(fh).save(str(out / bdf.stem))
    print(f"built {len(list(out.glob('*.pil')))} bitmap fonts -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
