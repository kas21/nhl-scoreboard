"""College hockey game board: the NHL layout with school logos and colours, the poll rank in
front of each record (``#3 12-4-1``), and no power-play or empty-net indicators — ESPN's feed
does not carry the situation, so the shared indicator code sees nothing to draw."""
from __future__ import annotations

from typing import Any

from PIL import Image
from pydantic import ConfigDict

from ...nhl.boards.game import GameBoard as NhlGameBoard
from ...nhl.boards.game import GameConfig
from ..teams import colors, logo, text_on


def with_ranks(g: dict[str, Any]) -> dict[str, Any]:
    """The game with ``#n`` in front of each ranked side's record."""
    sides = {}
    for side in ("away", "home"):
        s = g[side]
        rank = s.get("rank")
        sides[side] = {**s, "record": f"#{rank} {s.get('record', '')}".strip()} if rank else s
    return {**g, **sides}


class NcaahGameConfig(GameConfig):
    model_config = ConfigDict(frozen=True, extra="forbid", title="College hockey game board")
    show_rank: bool = True


class NcaahGameBoard(NhlGameBoard):
    key = "ncaah.game"
    title = "College hockey game"
    config_model = NcaahGameConfig
    sport = "ncaah"

    def logo_image(self, abbrev: str, g: dict[str, Any]) -> Image.Image:
        return logo(abbrev, 128)

    def side_colors(self, g: dict[str, Any], side: str):
        primary = tuple(g[side].get("color") or colors(g[side]["abbrev"])[0])
        return primary, text_on(primary)

    def _teams_info(self, g: dict[str, Any], cfg, f6) -> list:
        return super()._teams_info(with_ranks(g) if getattr(cfg, "show_rank", True) else g, cfg, f6)

    def _sog_row(self, g: dict[str, Any], f6) -> list:
        """ESPN only has shots once a game is under way; an all-zero row would just be wrong."""
        if not (g["away"].get("sog") or g["home"].get("sog")):
            return []
        return super()._sog_row(g, f6)
