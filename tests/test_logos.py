import asyncio
import io
import logging
import os

import httpx
import pytest
import respx
from PIL import Image

from scoreboard import imagecache, logos
from scoreboard.nfl import teams as nfl_teams
from scoreboard.nhl import teams as nhl_teams

LOG = logging.getLogger("test")
CDN = r"https://a\.espncdn\.com/i/teamlogos/.*"


def png_bytes(color=(10, 200, 40, 255), size=(500, 500)):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(logos, "LOGO_DIR", tmp_path)
    return tmp_path


def test_espn_codes_map_the_odd_ones_out():
    assert logos.espn_code("nhl", "TOR") == "tor"
    assert logos.espn_code("nhl", "LAK") == "la"        # ESPN kept the legacy short codes
    assert logos.espn_code("nhl", "SJS") == "sj"
    assert logos.espn_code("nhl", "TBL") == "tb"
    assert logos.espn_code("nfl", "WSH") == "wsh"       # NFL is the abbrev throughout


def test_is_png_rejects_junk():
    assert imagecache.is_png(png_bytes(size=(4, 4)))
    assert not imagecache.is_png(b"<html>404</html>")
    assert not imagecache.is_png(b"")
    assert not imagecache.is_png(b"\x89PNG\r\n\x1a\n" + b"x" * imagecache.MAX_PNG_BYTES)


@pytest.mark.asyncio
async def test_prefetch_downloads_once_then_skips(cache):
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        route = mock.get(url__regex=CDN).mock(return_value=httpx.Response(200, content=png_bytes()))
        assert await logos.prefetch(http, "nhl", ("TOR", "MTL"), LOG) == 2
        assert route.call_count == 2
        assert (cache / "nhl" / "TOR.png").is_file() and (cache / "nhl" / "MTL.png").is_file()
        assert await logos.prefetch(http, "nhl", ("TOR", "MTL"), LOG) == 0    # already cached
        assert route.call_count == 2


@pytest.mark.asyncio
async def test_prefetch_survives_a_bad_response(cache):
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(url__regex=r".*/tor\.png").mock(return_value=httpx.Response(404))
        mock.get(url__regex=r".*/mtl\.png").mock(return_value=httpx.Response(200, content=b"<html>nope</html>"))
        mock.get(url__regex=CDN).mock(return_value=httpx.Response(200, content=png_bytes()))
        assert await logos.prefetch(http, "nhl", ("TOR", "MTL", "BOS"), LOG) == 1
    assert not (cache / "nhl" / "TOR.png").exists()
    assert not (cache / "nhl" / "MTL.png").exists()      # HTTP 200 is not proof of a PNG
    assert (cache / "nhl" / "BOS.png").is_file()


@pytest.mark.asyncio
async def test_prefetch_runs_concurrently_without_exceeding_the_limit(cache):
    live, peak = 0, 0

    async def slow(request):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        return httpx.Response(200, content=png_bytes(size=(8, 8)))

    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(url__regex=CDN).mock(side_effect=slow)
        assert await logos.prefetch(http, "nhl", tuple(f"T{i:02d}" for i in range(12)), LOG) == 12
    assert 1 < peak <= logos.CONCURRENCY


def test_logo_is_none_until_fetched_then_picks_the_file_up(cache):
    assert logos.logo("nhl", "TOR", 32) is None
    (cache / "nhl").mkdir(parents=True)
    (cache / "nhl" / "TOR.png").write_bytes(png_bytes(color=(255, 0, 0, 255)))
    img = logos.logo("nhl", "TOR", 32)
    assert img is not None and img.size == (32, 32)
    assert img.getpixel((16, 16))[:3] == (255, 0, 0)

    # a re-fetch replaces the file: the mtime-keyed cache must not serve the old art
    (cache / "nhl" / "TOR.png").write_bytes(png_bytes(color=(0, 0, 255, 255)))
    os.utime(cache / "nhl" / "TOR.png", (1, 1))
    assert logos.logo("nhl", "TOR", 32).getpixel((16, 16))[:3] == (0, 0, 255)


def test_teams_fall_back_to_a_placeholder(cache):
    for registry, abbrev in ((nhl_teams, "TOR"), (nfl_teams, "BUF")):
        img = registry.logo(abbrev, 24)
        assert img.size == (24, 24) and img.getbbox() is not None      # drawn, not blank
    (cache / "nhl").mkdir(parents=True)
    (cache / "nhl" / "TOR.png").write_bytes(png_bytes(color=(255, 0, 0, 255)))
    assert nhl_teams.logo("TOR", 24).getpixel((12, 12))[:3] == (255, 0, 0)
