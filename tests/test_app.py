"""The render loop's failure handling.

Catching per frame is right — one bad board should not take the sign down — but the
watchdog in `run_async` only notices a render thread that has *died*. A thread that is
alive and failing every single frame looked healthy forever: a black panel, a service
systemd is happy with, and nothing to restart it. So a run of consecutive failures has
to end the process and let `Restart=always` do its job.

The limit is patched down to a handful here; at the real 300 these would spend their
time asleep between frames, proving nothing extra.
"""
import threading

import pytest
from PIL import Image

from scoreboard import app as app_module
from scoreboard.app import RENDER_FAILURE_EXIT_CODE, Application

LIMIT = 5


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_CONSECUTIVE_RENDER_FAILURES", LIMIT)
    a = Application(tmp_path / "config.json", output_mode="none")
    a.config.update({"display": {"fps": 60}})
    yield a
    a._stop.set()


def run(a) -> threading.Thread:
    t = threading.Thread(target=a.render_loop, daemon=True)
    t.start()
    t.join(timeout=10)
    return t


def test_a_wedged_render_loop_gives_up_so_the_service_manager_can_restart(app):
    calls = []

    def always_fails(_mono=None):
        calls.append(1)
        raise RuntimeError("every board is broken")

    app.director.frame = always_fails
    assert not run(app).is_alive(), "the loop should have stopped, not spun forever"
    assert app.exit_code == RENDER_FAILURE_EXIT_CODE
    assert len(calls) == LIMIT


def test_an_occasional_bad_frame_is_absorbed(app):
    """A transient failure must not count toward the limit once drawing recovers, so a
    board that fails far more often than it succeeds still never trips it."""
    calls = []

    def flaky(_mono=None):
        calls.append(1)
        if len(calls) >= LIMIT * 6:
            app._stop.set()
        if len(calls) % 3:                          # fails twice, draws once, repeat
            raise RuntimeError("transient")
        return Image.new("RGB", (128, 64))

    app.director.frame = flaky
    assert not run(app).is_alive()
    assert app.exit_code == 0, "a loop that keeps recovering must never trip the limit"
    assert len(calls) >= LIMIT * 6                  # far more total failures than the limit
