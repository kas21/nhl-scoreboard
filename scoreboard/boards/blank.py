from PIL import Image

from .base import BaseBoard, BoardContext


class BlankBoard(BaseBoard):
    key = "blank"
    title = "Blank (screen off)"

    def render(self, ctx: BoardContext, cfg) -> Image.Image:
        return Image.new("RGB", (ctx.width, ctx.height), (0, 0, 0))
