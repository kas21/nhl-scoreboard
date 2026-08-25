"""Frame sink: real LED matrix, the RGBMatrixEmulator, or nothing (tests)."""
from __future__ import annotations

import logging
from typing import Protocol

from PIL import Image

from ..config.models import DisplayConfig

log = logging.getLogger(__name__)


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
        options.rows = cfg.height // cfg.parallel
        options.cols = cfg.width // cfg.chain
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

    def show(self, frame: Image.Image) -> None:
        self._canvas.SetImage(frame.convert("RGB"))
        self._canvas = self._matrix.SwapOnVSync(self._canvas)

    def set_brightness(self, percent: int) -> None:
        if percent != self._brightness:
            self._brightness = percent
            self._matrix.brightness = percent

    def close(self) -> None:
        try:
            self._matrix.Clear()
        except Exception:  # noqa: BLE001
            pass


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
