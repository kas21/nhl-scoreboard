import asyncio
import io
import logging
import os

import httpx
import pytest
import respx
from PIL import Image

from scoreboard import espn, imagecache, logos
from scoreboard.nfl import teams as nfl_teams
from scoreboard.nhl import teams as nhl_teams

LOG = logging.getLogger("test")
CDN = r"https://a\.espncdn\.com/i/teamlogos/.*"


def png_bytes(color=(10, 200, 40, 255), size=(500, 500)):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def prefs():
    """Logo preferences are module state; every test starts and leaves them stock."""
    logos.apply_config(True, {})
    yield
    logos.apply_config(True, {})


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
    # MTL/BOS carry no curated variant, so this stays a pure default-variant test
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        route = mock.get(url__regex=CDN).mock(return_value=httpx.Response(200, content=png_bytes()))
        assert await logos.prefetch(http, "nhl", ("BOS", "MTL"), LOG) == 2
        assert route.call_count == 2
        assert (cache / "nhl" / "BOS.png").is_file() and (cache / "nhl" / "MTL.png").is_file()
        assert await logos.prefetch(http, "nhl", ("BOS", "MTL"), LOG) == 0    # already cached
        assert route.call_count == 2


@pytest.mark.asyncio
async def test_prefetch_survives_a_bad_response(cache):
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(url__regex=r".*/nyr\.png").mock(return_value=httpx.Response(404))
        mock.get(url__regex=r".*/mtl\.png").mock(return_value=httpx.Response(200, content=b"<html>nope</html>"))
        mock.get(url__regex=CDN).mock(return_value=httpx.Response(200, content=png_bytes()))
        assert await logos.prefetch(http, "nhl", ("NYR", "MTL", "BOS"), LOG) == 1
    assert not (cache / "nhl" / "NYR.png").exists()
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


# -- variants ---------------------------------------------------------------

TEAMS_API = r"https://site\.api\.espn\.com/apis/site/v2/sports/.*"
GUID = "https://a.espncdn.com/guid/abc/logos/{}.png"


def teams_payload(*teams):
    return {"sports": [{"leagues": [{"teams": [
        {"team": {"abbreviation": ab, "logos": [
            {"rel": ["full", "default"], "href": f"https://a.espncdn.com/i/teamlogos/nhl/500/{ab.lower()}.png"},
            {"rel": ["full", "secondary_logo_on_black_color"], "href": GUID.format("secondary_logo_on_black_color")},
            {"rel": ["full", "primary_logo_on_black_color"], "href": GUID.format("primary_logo_on_black_color")},
        ]}} for ab in teams]}]}]}


def test_variant_paths_keep_the_default_filename(cache):
    assert logos.path("nhl", "WSH").name == "WSH.png"                       # old caches still count
    assert logos.path("nhl", "WSH", "secondary_on_black").name == "WSH__secondary_on_black.png"


def test_api_abbrevs_cover_the_teams_espn_names_differently():
    assert logos.api_abbrev("nhl", "TOR") == "TOR"
    assert logos.api_abbrev("nhl", "NJD") == "NJ"       # the team API disagrees with the CDN here
    assert logos.api_abbrev("nhl", "UTA") == "UTAH"
    assert logos.api_abbrev("nhl", "LAK") == "LA"


def test_curated_defaults_apply_and_can_be_switched_off():
    assert logos.preference("nhl", "WSH") == "secondary_on_black"   # audited: primary is a wordmark
    assert logos.preference("nhl", "TOR") == "primary_on_black"     # audited: navy on black
    assert logos.preference("nhl", "MTL") == "default"              # the spoked-B tier needs no help
    logos.apply_config(False, {})
    assert logos.preference("nhl", "WSH") == "default"


def test_overrides_beat_curated_defaults_and_junk_is_ignored():
    logos.apply_config(True, {"nhl:WSH": "primary_white", "nhl:MTL": "not-a-variant"})
    assert logos.preference("nhl", "WSH") == "primary_white"
    assert logos.preference("nhl", "MTL") == "default"
    assert logos.preference("nfl", "WSH") == "default"              # keyed per sport: NFL has a WSH too


def test_config_change_bumps_the_generation_watchers_poll():
    before = logos.generation()
    logos.apply_config(True, {"nhl:WSH": "primary_white"})
    assert logos.generation() > before


@pytest.mark.asyncio
async def test_prefetch_pulls_the_preferred_variant_alongside_the_default(cache):
    logos.apply_config(True, {"nhl:TOR": "secondary_on_black"})
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(url__regex=TEAMS_API).mock(return_value=httpx.Response(200, json=teams_payload("TOR")))
        mock.get(url__regex=r"https://a\.espncdn\.com/.*").mock(return_value=httpx.Response(200, content=png_bytes()))
        assert await logos.prefetch(http, "nhl", ("TOR",), LOG) == 2
    assert logos.path("nhl", "TOR").is_file()
    assert logos.path("nhl", "TOR", "secondary_on_black").is_file()


@pytest.mark.asyncio
async def test_prefetch_skips_discovery_when_no_variant_is_wanted(cache):
    logos.apply_config(False, {})       # everything on the default variant
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        api = mock.get(url__regex=TEAMS_API).mock(return_value=httpx.Response(200, json=teams_payload("TOR")))
        mock.get(url__regex=CDN).mock(return_value=httpx.Response(200, content=png_bytes()))
        assert await logos.prefetch(http, "nhl", ("TOR",), LOG) == 1
    assert not api.called          # the team API is only worth a request when a variant needs it


@pytest.mark.asyncio
async def test_a_dead_team_api_leaves_the_default_art_working(cache):
    logos.apply_config(True, {"nhl:TOR": "secondary_on_black"})
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(url__regex=TEAMS_API).mock(return_value=httpx.Response(503))
        mock.get(url__regex=CDN).mock(return_value=httpx.Response(200, content=png_bytes()))
        assert await logos.prefetch(http, "nhl", ("TOR",), LOG) == 1     # default still lands
    assert logos.logo("nhl", "TOR", 32) is not None                      # and boards still get art


def test_logo_falls_back_to_the_default_until_the_variant_lands(cache):
    logos.apply_config(True, {"nhl:TOR": "secondary_on_black"})
    logos.path("nhl", "TOR").parent.mkdir(parents=True, exist_ok=True)
    logos.path("nhl", "TOR").write_bytes(png_bytes(color=(255, 0, 0, 255)))
    assert logos.logo("nhl", "TOR", 32).getpixel((16, 16))[:3] == (255, 0, 0)
    logos.path("nhl", "TOR", "secondary_on_black").write_bytes(png_bytes(color=(0, 255, 0, 255)))
    assert logos.logo("nhl", "TOR", 32).getpixel((16, 16))[:3] == (0, 255, 0)   # preferred art wins once cached


@pytest.mark.asyncio
async def test_oversized_art_is_shrunk_before_it_reaches_the_cache(cache):
    logos.apply_config(True, {"nhl:TOR": "secondary_on_black"})
    big = png_bytes(size=(4096, 4096))
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(url__regex=TEAMS_API).mock(return_value=httpx.Response(200, json=teams_payload("TOR")))
        mock.get(url__regex=r"https://a\.espncdn\.com/.*").mock(return_value=httpx.Response(200, content=big))
        await logos.prefetch(http, "nhl", ("TOR",), LOG)
    with Image.open(logos.path("nhl", "TOR", "secondary_on_black")) as im:
        assert max(im.size) == logos.STORE_EDGE      # a Pi never decodes 4096px


@pytest.mark.asyncio
async def test_discovery_sends_a_user_agent_espn_accepts(cache):
    """ESPN's site API 403s custom user agents, so discovery must not inherit the app's."""
    logos.apply_config(True, {"nhl:TOR": "secondary_on_black"})
    seen = {}

    def capture(request):
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json=teams_payload("TOR"))

    async with httpx.AsyncClient(headers={"User-Agent": "nhl-scoreboard"}) as http, respx.mock() as mock:
        mock.get(url__regex=TEAMS_API).mock(side_effect=capture)
        mock.get(url__regex=r"https://a\.espncdn\.com/.*").mock(return_value=httpx.Response(200, content=png_bytes()))
        await logos.prefetch(http, "nhl", ("TOR",), LOG)
    assert seen["ua"] == espn.API_UA and "nhl-scoreboard" not in seen["ua"]
