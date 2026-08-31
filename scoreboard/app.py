"""Wires everything together: config → sources → snapshot → director → output + web."""
from __future__ import annotations

import asyncio
import logging
import signal
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from . import logos
from .config import ConfigStore
from .data import SnapshotStore
from .data.arbiter import MainEventArbiter
from .data.events import EventBus
from .data.health import SourceHealth
from .data.source import SourceContext, run_source_forever
from .director import Director
from .output import PreviewHub, create_output
from .plugins import load_registry
from .web.api import LogBuffer, SystemControl, create_app
from .web.updater import Updater

log = logging.getLogger(__name__)

# The render loop absorbs a bad frame, because one board misbehaving should not take the
# sign down. But absorbing *every* frame forever is a black panel on a service that still
# looks healthy, and the watchdog in run_async only catches a thread that has died. After
# this many failures in a row, stop and let the service manager restart us cleanly.
MAX_CONSECUTIVE_RENDER_FAILURES = 300           # ~10s at the default 30fps
RENDER_FAILURE_EXIT_CODE = 4


class Application:
    def __init__(self, config_path: Path, output_mode: str = "auto", demo: bool = False) -> None:
        self.logs = LogBuffer()
        logging.getLogger().addHandler(self.logs)
        self.config = ConfigStore(config_path)
        logging.getLogger().setLevel(self.config.get().log_level)
        self.config.subscribe(lambda cfg: logging.getLogger().setLevel(cfg.log_level))
        self._apply_logos(self.config.get())
        self.config.subscribe(self._apply_logos)
        self.snapshots = SnapshotStore()
        self.health = SourceHealth()
        self.events = EventBus()
        self.snapshots.subscribe(self.events.on_snapshot)
        self.arbiter = MainEventArbiter(self.snapshots, lambda: self.config.get().sports.priority)
        self.registry = load_registry()
        if demo:
            from .demo import DemoSource

            log.warning("DEMO MODE: replaying a recorded game")
            self.registry.sources = {**self.registry.sources, "nhl": DemoSource()}
        for detector in self.registry.detectors:
            self.events.register(detector)
        self.director = Director(self.config, self.snapshots, self.registry, self.events)
        self.preview = PreviewHub(self.config.get().web.preview_fps)
        self.config.subscribe(lambda c: self.preview.set_fps(c.web.preview_fps))
        self.output = create_output(self.config.get().display, output_mode, self.director.brightness())
        self._stop = threading.Event()
        self.exit_code = 0
        self._restart_requested = threading.Event()
        self.updater = Updater(restart=self.request_restart,
                               allow_unowned=lambda: self.config.get().web.allow_unowned_checkout)

    @staticmethod
    def _apply_logos(cfg) -> None:
        """Boards resolve their own logo variant, so the choice only has to land here."""
        logos.apply_config(cfg.logos.use_curated_defaults, cfg.logos.overrides)

    def request_restart(self) -> None:
        """Exit cleanly; under systemd (Restart=always) that is a restart with fresh driver options."""
        log.info("restart requested from the web UI")
        self._restart_requested.set()

    # -- render thread -------------------------------------------------------

    def render_loop(self) -> None:
        log.info("render loop started")
        try:
            self._render_loop()
        except BaseException:
            log.exception("render loop crashed")
            raise

    def _render_loop(self) -> None:
        failures = 0
        while not self._stop.is_set():
            started = time.monotonic()
            cfg = self.config.get()
            try:
                frame = self.director.frame(started)
                self.output.set_brightness(self.director.brightness())
                self.output.show(frame)
                self.preview.submit(frame)
                failures = 0
            except Exception:
                failures += 1
                log.exception("render loop error (%s in a row)", failures)
                if failures >= MAX_CONSECUTIVE_RENDER_FAILURES:
                    log.critical("render loop failed %s frames in a row; exiting so the "
                                 "service manager restarts us", failures)
                    self.exit_code = RENDER_FAILURE_EXIT_CODE
                    break
            budget = 1.0 / cfg.display.fps
            time.sleep(max(budget - (time.monotonic() - started), 0.0))
        self.output.close()

    # -- asyncio side --------------------------------------------------------

    async def run_async(self) -> None:
        loop = asyncio.get_running_loop()
        self.preview.attach_loop(loop)
        render_thread = threading.Thread(target=self.render_loop, name="render", daemon=True)
        render_thread.start()

        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": "nhl-scoreboard"}) as http:
            tasks = [asyncio.create_task(run_source_forever(src, self._context(key, src, http)), name=f"source:{key}")
                     for key, src in self.registry.sources.items()]
            web = self.config.get().web
            server = uvicorn.Server(uvicorn.Config(
                create_app(self.config, self.snapshots, self.registry, self.director, self.preview, self.logs,
                           system=SystemControl(self.request_restart), updater=self.updater, health=self.health),
                host=web.host, port=web.port, log_level="warning", loop="asyncio",
            ))
            server.install_signal_handlers = lambda: None  # we handle signals ourselves
            web_task = asyncio.create_task(server.serve(), name="web")
            log.info("web UI on http://%s:%s", web.host, web.port)

            stop = asyncio.Event()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, stop.set)
                except NotImplementedError:
                    pass

            async def watch_render_thread() -> None:
                while not stop.is_set():
                    await asyncio.sleep(1)
                    if self._restart_requested.is_set():
                        stop.set()
                    if not render_thread.is_alive():
                        log.critical("render thread died; exiting so the service manager restarts us")
                        self.exit_code = 3
                        stop.set()

            tasks.append(asyncio.create_task(watch_render_thread(), name="render-watchdog"))

            async def update_checker() -> None:
                await asyncio.sleep(30)
                while not stop.is_set():
                    hours = self.config.get().web.update_check_hours
                    if hours > 0 and self.updater.is_checkout:
                        await asyncio.to_thread(self.updater.check)
                    await asyncio.sleep(max(hours, 1) * 3600 if hours > 0 else 3600)

            tasks.append(asyncio.create_task(update_checker(), name="update-checker"))
            await stop.wait()
            log.info("shutting down")
            server.should_exit = True
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await web_task
        self._stop.set()
        render_thread.join(timeout=2)

    def _context(self, key: str, source, http: httpx.AsyncClient) -> SourceContext:
        def cfg_getter():
            raw = self.config.get().sources.get(key, {})
            try:
                return source.config_model.model_validate(raw)
            except Exception:
                log.warning("invalid config for source %s, using defaults", key)
                return source.config_model()
        ctx = SourceContext(key, self.snapshots, cfg_getter, http, health=self.health)
        def apply_location(c) -> None:
            ctx.timezone = c.location.timezone
            ctx.location = (c.location.latitude, c.location.longitude) if c.location.latitude is not None and c.location.longitude is not None else None
        apply_location(self.config.get())
        self.config.subscribe(apply_location)
        return ctx

    def run(self) -> None:
        asyncio.run(self.run_async())
        if self.exit_code:
            raise SystemExit(self.exit_code)
