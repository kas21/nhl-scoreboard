"""``scoreboard`` console entry point."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".scoreboard" / "config.json"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="scoreboard", description="LED matrix scoreboard")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="path to config.json")
    parser.add_argument("--output", choices=["auto", "hardware", "emulator", "none"], default="auto",
                        help="frame sink (default: hardware if available, else emulator, else none)")
    parser.add_argument("--emulator", action="store_const", const="emulator", dest="output", help="shortcut for --output emulator")
    parser.add_argument("--demo", action="store_true", help="replay a recorded game instead of polling the NHL")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from .app import Application

    Application(args.config, args.output, demo=args.demo).run()


if __name__ == "__main__":
    main()
