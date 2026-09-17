"""AHL game board: the NHL layout with the affiliates' logos and colours. The feed's own
period, clock and shots fill the same slots; the power play and its clock come from the
penalty log the source reconstructs, so the indicators light up as they do for the NHL."""
from __future__ import annotations

from typing import Any

from PIL import Image
from pydantic import ConfigDict

from ...nhl.boards.game import GameBoard as NhlGameBoard
from ...nhl.boards.game import GameConfig
from ..teams import colors, logo, text_on


class AhlGameConfig(GameConfig):
    model_config = ConfigDict(frozen=True, extra="forbid", title="AHL game board")


class AhlGameBoard(NhlGameBoard):
    key = "ahl.game"
    title = "AHL game"
    config_model = AhlGameConfig
    sport = "ahl"

    def logo_image(self, abbrev: str, g: dict[str, Any]) -> Image.Image:
        return logo(abbrev, 128)

    def side_colors(self, g: dict[str, Any], side: str):
        primary, _ = colors(g[side]["abbrev"])
        return primary, text_on(primary)

    def _sog_row(self, g: dict[str, Any], f6) -> list:
        """Shots come from the game summary the source fetches for the followed game; a slate game has none."""
        if not (g["away"].get("sog") or g["home"].get("sog")):
            return []
        return super()._sog_row(g, f6)
