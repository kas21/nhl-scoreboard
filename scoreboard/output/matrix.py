"""Frame sink: real LED matrix, the RGBMatrixEmulator, or nothing (tests)."""
from __future__ import annotations

import logging
from typing import Protocol

from PIL import Image

from ..config.models import DisplayConfig

log = logging.getLogger(__name__)


def rotated_quarter(pixel_mapper: str) -> bool:
    """True when the mapper string turns the picture by 90 or 270 degrees (``Rotate:90``,
    ``U-mapper;Rotate:270``): rpi-rgb-led-matrix's rotate mapper swaps the canvas for those."""
    for part in pixel_mapper.split(";"):
        name, _, arg = part.strip().partition(":")
        if name.strip().lower() == "rotate":
            try:
                return int(arg.strip() or 0) % 180 == 90
            except ValueError:
                return False
    return False


class Output(Protocol):
    def show(self, frame: Image.Image) -> None: ...
    def set_brightness(self, percent: int) -> None: ...
    def close(self) -> None: ...


class NullOutput:
    def __init__(self) -> None:
        self.last: Image.Image | None = None
        self.brightness = 100

    def show(self, frame: Image.Image) -> None:
        self.last = frame

    def set_brightness(self, percent: int) -> None:
        self.brightness = percent

    def close(self) -> None:
        pass


class MatrixOutput:
    """Wraps rgbmatrix (hardware) or RGBMatrixEmulator (same API)."""

    def __init__(self, cfg: DisplayConfig, emulator: bool, brightness: int = 80) -> None:
        if emulator:
            from RGBMatrixEmulator import RGBMatrix, RGBMatrixOptions  # type: ignore
        else:
            from rgbmatrix import RGBMatrix, RGBMatrixOptions  # type: ignore
        options = RGBMatrixOptions()
        # display.width/height are the picture you see. A Rotate:90/270 mapper turns the
        # physical panel on its side, so the driver has to be told the panel's own rows and
        # columns, which are then the configured size swapped; the canvas it hands back is
        # the configured size again, which is what the director renders.
        phys_w, phys_h = (cfg.height, cfg.width) if rotated_quarter(cfg.pixel_mapper) else (cfg.width, cfg.height)
        options.rows = phys_h // cfg.parallel
        options.cols = phys_w // cfg.chain
        options.chain_length = cfg.chain
        options.parallel = cfg.parallel
        options.hardware_mapping = cfg.gpio_mapping
        options.brightness = brightness
        options.pwm_bits = cfg.pwm_bits
        options.pwm_lsb_nanoseconds = cfg.pwm_lsb_nanoseconds
        options.pwm_dither_bits = cfg.pwm_dither_bits
        options.gpio_slowdown = cfg.slowdown_gpio
        options.limit_refresh_rate_hz = cfg.limit_refresh
        options.scan_mode = cfg.scan_mode
        options.row_address_type = cfg.row_addr_type
        options.multiplexing = cfg.multiplexing
        options.led_rgb_sequence = cfg.rgb_sequence
        options.drop_privileges = cfg.drop_privileges
        if cfg.panel_type:
            options.panel_type = cfg.panel_type
        if cfg.pixel_mapper:
            options.pixel_mapper_config = cfg.pixel_mapper
        self._matrix = RGBMatrix(options=options)
        self._canvas = self._matrix.CreateFrameCanvas()
        self._brightness = brightness
        got = (getattr(self._matrix, "width", None), getattr(self._matrix, "height", None))
        if None not in got and got != (cfg.width, cfg.height):
            log.warning("the matrix driver made a %sx%s canvas but display.width/height say %sx%s; "
                        "frames will be cropped or padded. Check chain/parallel and the pixel mapper", *got, cfg.width, cfg.height)

    def show(self, frame: Image.Image) -> None:
        self._canvas.SetImage(frame.convert("RGB"))
        self._canvas = self._matrix.SwapOnVSync(self._canvas)

    def set_brightness(self, percent: int) -> None:
        if percent != self._brightness:
            self._brightness = percent
            self._matrix.brightness = percent

    def close(self) -> None:
        """Blank the panel and destroy the matrix. The driver stops its refresh thread and
        resets GPIO in the C++ destructor, which the binding runs when the object is freed;
        Clear() alone left the refresh thread driving the panel until the interpreter got
        round to it, and a non-clean exit never did. Safe to call twice."""
        matrix = getattr(self, "_matrix", None)
        if matrix is None:
            return
        try:
            matrix.Clear()
        except Exception:
            pass
        self._canvas = None
        self._matrix = None
        del matrix


def create_output(cfg: DisplayConfig, mode: str, brightness: int = 80) -> Output:
    """``mode`` is 'auto' | 'hardware' | 'emulator' | 'none'."""
    if mode == "none":
        return NullOutput()
    if mode == "emulator":
        return MatrixOutput(cfg, emulator=True, brightness=brightness)
    if mode == "hardware":
        return MatrixOutput(cfg, emulator=False, brightness=brightness)
    try:
        return MatrixOutput(cfg, emulator=False, brightness=brightness)
    except ImportError:
        log.info("rgbmatrix not available, trying emulator")
    try:
        return MatrixOutput(cfg, emulator=True, brightness=brightness)
    except ImportError:
        log.warning("no matrix output available; running headless (browser preview only)")
        return NullOutput()
