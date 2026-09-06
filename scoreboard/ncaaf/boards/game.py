"""College football game board: the NFL board with FBS logos and colours, plus the poll rank
in front of each record (``#3 2-0``)."""
from __future__ import annotations

from typing import Any

from PIL import Image
from pydantic import ConfigDict

from ...nfl.boards.game import NflGameBoard, NflGameConfig
from ..teams import logo


def with_ranks(g: dict[str, Any]) -> dict[str, Any]:
    """The game with ``#n`` in front of each ranked side's record."""
    sides = {}
    for side in ("away", "home"):
        s = g[side]
        rank = s.get("rank")
        sides[side] = {**s, "record": f"#{rank} {s.get('record', '')}".strip()} if rank else s
    return {**g, **sides}


class NcaafGameConfig(NflGameConfig):
    model_config = ConfigDict(frozen=True, extra="forbid", title="College football game board")
    show_rank: bool = True


class NcaafGameBoard(NflGameBoard):
    key = "ncaaf.game"
    title = "College football game"
    config_model = NcaafGameConfig
    sport = "ncaaf"

    def logo_image(self, abbrev: str, g: dict[str, Any]) -> Image.Image:
        return logo(abbrev, 128)

    def _teams_info(self, g: dict[str, Any], cfg, f6) -> list:
        return super()._teams_info(with_ranks(g) if getattr(cfg, "show_rank", True) else g, cfg, f6)
