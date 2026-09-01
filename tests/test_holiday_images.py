"""Uploading a picture for a holiday: storage rules, then the endpoints over them."""
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from scoreboard.boards.clock import ClockBoard
from scoreboard.config import ConfigStore
from scoreboard.data import SnapshotStore
from scoreboard.data.events import EventBus
from scoreboard.director import Director
from scoreboard.extras.holidays import images
from scoreboard.extras.holidays.images import ImageError
from scoreboard.output import PreviewHub
from scoreboard.plugins import Registry
from scoreboard.web.api import create_app
from scoreboard.web.guard import UI_HEADER, UI_TOKEN

UI = {"headers": {UI_HEADER: UI_TOKEN}, "base_url": "http://localhost"}


@pytest.fixture(autouse=True)
def user_images(tmp_path, monkeypatch):
    """Every test gets its own empty upload directory."""
    path = tmp_path / "user-images"
    monkeypatch.setattr(images, "USER_IMAGES", path)
    return path


def png_bytes(size=(400, 300), colour=(10, 200, 40, 255), fmt="PNG"):
    buf = BytesIO()
    Image.new("RGBA" if fmt == "PNG" else "RGB", size, colour[: 4 if fmt == "PNG" else 3]).save(buf, fmt)
    return buf.getvalue()


# -- storage -------------------------------------------------------------------


def test_save_normalises_to_a_small_rgba_png(user_images):
    path = images.save("puck_drop", png_bytes(size=(1200, 900), fmt="JPEG"))
    assert path == user_images / "puck_drop.png"
    with Image.open(path) as saved:
        assert saved.format == "PNG" and saved.mode == "RGBA"
        assert max(saved.size) == images.STORED_SIZE
        assert saved.size[0] / saved.size[1] == pytest.approx(4 / 3, abs=0.02)   # aspect kept


def test_save_leaves_a_picture_that_is_already_small_alone():
    with Image.open(images.save("puck_drop", png_bytes(size=(40, 20)))) as saved:
        assert saved.size == (40, 20)


def test_save_rejects_a_file_that_is_not_a_picture():
    with pytest.raises(ImageError):
        images.save("puck_drop", b"#!/bin/sh\nrm -rf /\n")


def test_save_rejects_an_empty_upload():
    with pytest.raises(ImageError):
        images.save("puck_drop", b"")


def test_save_rejects_more_bytes_than_we_will_hold(monkeypatch):
    monkeypatch.setattr(images, "MAX_UPLOAD_BYTES", 64)
    with pytest.raises(ImageError):
        images.save("puck_drop", png_bytes())


def test_save_rejects_a_decompression_bomb_before_decoding_it(monkeypatch):
    """The header is read first, so an absurd pixel count never reaches the decoder."""
    monkeypatch.setattr(images, "MAX_SOURCE_PIXELS", 100)
    with pytest.raises(ImageError):
        images.save("puck_drop", png_bytes(size=(400, 300)))


@pytest.mark.parametrize("bad", ["../../etc/passwd", "a/b", "Christmas Day", "", "x" * 65, "a.png"])
def test_save_refuses_anything_that_is_not_a_slug(bad, user_images):
    with pytest.raises(ImageError):
        images.save(bad, png_bytes())
    assert not user_images.exists() or list(user_images.iterdir()) == []


def test_an_uploaded_picture_shadows_the_bundled_one_and_delete_restores_it(user_images):
    bundled = images.resolve("christmas_day")
    assert bundled is not None and bundled.parent == images.IMAGES
    images.save("christmas_day", png_bytes())
    assert images.resolve("christmas_day") == user_images / "christmas_day.png"
    assert images.remove("christmas_day") is True
    assert images.resolve("christmas_day") == bundled            # the shipped art is untouched
    assert bundled.exists()


def test_removing_a_picture_that_was_never_uploaded_is_not_an_error():
    assert images.remove("christmas_day") is False
    assert images.resolve("christmas_day") is not None


def test_remove_refuses_a_slug_it_would_not_have_written():
    with pytest.raises(ImageError):
        images.remove("../../../etc/passwd")


# -- endpoints -----------------------------------------------------------------


def client(tmp_path):
    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={"clock": ClockBoard()})
    director = Director(config, snapshots, reg, events)
    app = create_app(config, snapshots, reg, director, PreviewHub())
    return TestClient(app, **UI), config, snapshots


def upload(c, slug, data=None):
    """The picture is the request body itself; see web/holidays.py."""
    return c.post(f"/api/holidays/images/{slug}", content=data if data is not None else png_bytes(),
                  headers={"content-type": "image/png"})


def test_upload_then_serve_then_delete(tmp_path, user_images):
    c, _, _ = client(tmp_path)
    assert c.get("/api/holidays/images/puck_drop").status_code == 404
    r = upload(c, "puck_drop")
    assert r.status_code == 200 and r.json()["slug"] == "puck_drop"
    served = c.get("/api/holidays/images/puck_drop")
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert served.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.delete("/api/holidays/images/puck_drop").json()["removed"] is True
    assert c.get("/api/holidays/images/puck_drop").status_code == 404


def test_get_falls_back_to_the_bundled_picture(tmp_path):
    c, _, _ = client(tmp_path)
    assert c.get("/api/holidays/images/christmas_day").status_code == 200


def test_deleting_a_bundled_picture_only_drops_the_upload_over_it(tmp_path):
    c, _, _ = client(tmp_path)
    upload(c, "christmas_day")
    assert c.delete("/api/holidays/images/christmas_day").json()["removed"] is True
    assert c.get("/api/holidays/images/christmas_day").status_code == 200      # shipped art still there


def test_a_junk_upload_is_refused_with_a_reason(tmp_path, user_images):
    c, _, _ = client(tmp_path)
    r = upload(c, "puck_drop", data=b"not a picture at all")
    assert r.status_code == 422 and "picture" in r.json()["detail"]
    assert not user_images.exists() or list(user_images.iterdir()) == []


def test_an_oversized_upload_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "MAX_UPLOAD_BYTES", 64)
    c, _, _ = client(tmp_path)
    assert upload(c, "puck_drop").status_code == 413


@pytest.mark.parametrize("slug", ["..%2f..%2fetc%2fpasswd", "Christmas%20Day", "a.png", "..", "%2e%2e%2f"])
def test_a_slug_that_is_not_a_slug_gets_nowhere(tmp_path, slug, user_images):
    """Refused by the handler, or routed nowhere at all — either way nothing is written."""
    c, _, _ = client(tmp_path)
    assert upload(c, slug).status_code in (404, 422)
    assert c.get(f"/api/holidays/images/{slug}").status_code in (404, 422)
    assert c.delete(f"/api/holidays/images/{slug}").status_code in (404, 422)
    assert not user_images.exists() or list(user_images.iterdir()) == []


def test_uploads_need_the_ui_header(tmp_path):
    """Same CSRF rule as every other state-changing call; see web/guard.py."""
    c, _, _ = client(tmp_path)
    bare = TestClient(c.app, base_url="http://localhost")
    assert bare.post("/api/holidays/images/puck_drop", content=png_bytes()).status_code == 403
    assert bare.delete("/api/holidays/images/puck_drop").status_code == 403


def test_a_new_picture_shows_up_without_waiting_for_the_next_refresh(tmp_path, user_images):
    """The source only recomputes hourly, so the endpoint has to republish itself."""
    c, config, snapshots = client(tmp_path)
    config.update({"sources": {"holidays": {"country": "US", "horizon_days": 365}}})
    assert snapshots.get().get("holidays.upcoming") is None      # no source is running here

    upload(c, "labor_day")
    after = {i["name"]: i for i in snapshots.get().get("holidays.upcoming")}
    assert after["Labor Day"]["image"] == str(user_images / "labor_day.png")
    assert {i["name"] for i in snapshots.get().get("holidays.available")} >= {"Labor Day"}

    c.delete("/api/holidays/images/labor_day")
    reverted = {i["name"]: i for i in snapshots.get().get("holidays.upcoming")}
    assert reverted["Labor Day"]["image"] is None
