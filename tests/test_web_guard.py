"""The two browser-driven attacks that reach an API with no login.

The scoreboard's API is unauthenticated on purpose — it is an appliance on a home
network, and a password on your own sign is friction with little to buy it. That
trade only holds if a *browser* cannot be tricked into driving the API on behalf of
whoever is sitting in front of it, so both ways that happens are shut here.

Drive-by CSRF: four state-changing endpoints take no request body, which makes them
CORS "simple requests" — any page on any site can POST them with no preflight, and
the action runs even though the reply is opaque to the attacker.

DNS rebinding: a name the attacker controls, re-pointed at the Pi, makes their page
same-origin. Every CORS rule stops applying, so the custom header above becomes
settable and the CSRF guard alone would not hold.
"""
from fastapi.testclient import TestClient

from scoreboard.boards.clock import ClockBoard
from scoreboard.boards.splash import SplashBoard
from scoreboard.config import ConfigStore
from scoreboard.data import SnapshotStore
from scoreboard.data.events import EventBus
from scoreboard.director import Director
from scoreboard.output import PreviewHub
from scoreboard.plugins import Registry
from scoreboard.web.api import SystemControl, create_app
from scoreboard.web.guard import UI_HEADER, UI_TOKEN

# Endpoints that take no body: sent cross-origin without a preflight, so they are
# exactly the ones a drive-by page can reach.
BODYLESS_POSTS = ("/api/config/reset", "/api/system/restart", "/api/system/update", "/api/system/update/check")


def build(tmp_path, **kw):
    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={b.key: b for b in (ClockBoard(), SplashBoard())})
    director = Director(config, snapshots, reg, events)
    app = create_app(config, snapshots, reg, director, PreviewHub(), **kw)
    return app, config


def raw(tmp_path, **kw):
    """A client that sends nothing extra — stands in for a page on another site."""
    app, config = build(tmp_path, **kw)
    return TestClient(app, base_url="http://localhost"), config


def ui(tmp_path, **kw):
    """A client that identifies itself the way the bundled UI does."""
    app, config = build(tmp_path, **kw)
    return TestClient(app, base_url="http://localhost", headers={UI_HEADER: UI_TOKEN}), config


# -- drive-by CSRF -------------------------------------------------------------

def test_bodyless_post_without_the_ui_header_is_refused(tmp_path):
    restarted = []
    c, config = raw(tmp_path, system=SystemControl(lambda: restarted.append(1)))
    config.update({"brightness": {"day": 33}})
    for path in BODYLESS_POSTS:
        assert c.post(path).status_code == 403, path
    assert restarted == []                                  # the action never ran
    assert config.get().brightness.day == 33                # /api/config/reset did not fire


def test_json_state_changes_also_need_the_header(tmp_path):
    c, config = raw(tmp_path)
    assert c.patch("/api/config", json={"brightness": {"day": 42}}).status_code == 403
    assert c.put("/api/config", json={}).status_code == 403
    assert c.post("/api/override", json={"board": "clock"}).status_code == 403
    assert config.get().brightness.day != 42


def test_the_ui_header_lets_state_changes_through(tmp_path):
    restarted = []
    c, config = ui(tmp_path, system=SystemControl(lambda: restarted.append(1)))
    assert c.patch("/api/config", json={"brightness": {"day": 42}}).status_code == 200
    assert config.get().brightness.day == 42
    assert c.post("/api/system/restart").status_code == 200
    assert restarted == [1]


def test_reading_never_needs_the_header(tmp_path):
    """A GET has no side effect, and blocking them would break nothing an attacker can read anyway."""
    c, _ = raw(tmp_path)
    for path in ("/", "/api/status", "/api/config", "/api/schema", "/api/boards", "/api/sources"):
        assert c.get(path).status_code == 200, path


def test_a_cross_origin_page_is_refused_even_holding_the_header(tmp_path):
    """Belt and braces: if a browser ever does send the header cross-origin, Origin still gives it away."""
    c, _ = ui(tmp_path)
    r = c.post("/api/system/restart", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert c.post("/api/system/restart", headers={"origin": "http://localhost"}).status_code == 200


# -- DNS rebinding -------------------------------------------------------------

def test_an_unknown_host_is_refused(tmp_path):
    """Rebinding needs a name that resolves to the Pi; refuse names we do not answer to."""
    c, _ = ui(tmp_path)
    for host in ("evil.example", "scoreboard.attacker.test", "rebind.evil.example:8080"):
        assert c.get("/api/config", headers={"host": host}).status_code == 403, host


def test_addresses_and_local_names_are_accepted(tmp_path):
    """Reaching the panel by IP or by <hostname>.local is the normal case, and a literal
    address cannot be rebound — rebinding is an attack on names."""
    import socket
    c, _ = ui(tmp_path)
    short = socket.gethostname().lower().rstrip(".").partition(".")[0]
    for host in ("localhost", "127.0.0.1", "127.0.0.1:8080", "192.168.1.42:8080", "[::1]:8080", short, f"{short}.local:8080"):
        assert c.get("/api/status", headers={"host": host}).status_code == 200, host


def test_an_operator_can_name_their_own_host(tmp_path):
    """Someone fronting the scoreboard with a real DNS name must not be locked out."""
    app, config = build(tmp_path)
    config.update({"web": {"allowed_hosts": ["sign.example.com"]}})
    c = TestClient(app, base_url="http://localhost", headers={UI_HEADER: UI_TOKEN})
    assert c.get("/api/status", headers={"host": "sign.example.com"}).status_code == 200
    assert c.get("/api/status", headers={"host": "other.example.com"}).status_code == 403


def test_the_websocket_preview_is_guarded_too(tmp_path):
    """The preview stream is a live picture of the panel; rebinding must not reach it either."""
    import pytest
    c, _ = ui(tmp_path)
    with pytest.raises(Exception), c.websocket_connect("/ws/preview", headers={"host": "evil.example"}):  # noqa: B017
        pass                                             # starlette closes the socket before accept
    with c.websocket_connect("/ws/preview", headers={"host": "localhost"}) as ws:
        assert ws is not None
