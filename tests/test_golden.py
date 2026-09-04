"""Golden frames: every board, in its key states, must draw exactly what it drew last time.

The 128x64 boards are pixel ports of the old client and the other profiles are tuned by eye,
so "renders without crashing" is not enough — a one-pixel drift in a font, a layout container
or an animation curve is a regression here. Boards are pure functions of the snapshot and
``ctx.elapsed``, which makes exact comparison cheap and portable.

    uv run pytest tests/test_golden.py                      # compare against tests/golden/
    SCOREBOARD_UPDATE_GOLDENS=1 uv run pytest tests/test_golden.py   # accept the current frames

A failure writes ``tests/golden/_failed/<board>/<state>@WxH.png``: expected | actual | diff,
scaled 4x so a 64x32 frame is readable. Look at it before regenerating — the point of the
suite is that a change to the look is a decision, not a side effect.

Team logos are synthetic here (a disc in the team's colours) so the goldens exercise the real
compositing path without depending on whatever the developer's cache has fetched.
"""
from __future__ import annotations

import os
import random
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from golden_scenes import Scene, all_scenes
from PIL import Image, ImageChops, ImageDraw

from scoreboard import logos
from scoreboard.nfl import teams as nfl_teams
from scoreboard.nhl import teams as nhl_teams
from scoreboard.plugins import load_registry

GOLDEN_DIR = Path(__file__).parent / "golden"
FAILED_DIR = GOLDEN_DIR / "_failed"
UPDATE = os.environ.get("SCOREBOARD_UPDATE_GOLDENS") == "1"
UPDATE_HINT = "SCOREBOARD_UPDATE_GOLDENS=1 uv run pytest tests/test_golden.py"
ZOOM = 4                    # failure sheets are scaled up so LED-sized frames are legible
LOGO_EDGE = 128
SEED = 20260411             # the fixture game day; any constant works, this one is memorable

SCENES = all_scenes()
CASES = [(scene, size) for scene in SCENES for size in scene.sizes]


def case_id(case: tuple[Scene, tuple[int, int]]) -> str:
    scene, (w, h) = case
    return f"{scene.name}@{w}x{h}"


def golden_path(scene: Scene, size: tuple[int, int]) -> Path:
    board, state = scene.name.split("/", 1)
    return GOLDEN_DIR / board / f"{state}@{size[0]}x{size[1]}.png"


# -- synthetic logos -------------------------------------------------------------


def _abbrevs(value: Any) -> Iterator[str]:
    """Every ``abbrev`` anywhere in a snapshot's data."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "abbrev" and isinstance(v, str):
                yield v.upper()
            else:
                yield from _abbrevs(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _abbrevs(v)


def _disc(primary: tuple[int, int, int], accent: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (LOGO_EDGE, LOGO_EDGE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, LOGO_EDGE - 1, LOGO_EDGE - 1), fill=(*primary, 255))
    inset = LOGO_EDGE // 5
    draw.ellipse((inset, inset, LOGO_EDGE - 1 - inset, LOGO_EDGE - 1 - inset), outline=(*accent, 255), width=LOGO_EDGE // 12)
    return img


@pytest.fixture(scope="module", autouse=True)
def synthetic_logos(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("golden-logos")
    seen = {a for scene in SCENES for a in _abbrevs(dict(scene.snapshot.data))}
    for abbrev in sorted(seen):
        nhl = nhl_teams.team(abbrev)
        primary, alternate = nfl_teams.colors(abbrev)
        for sport, art in (("nhl", _disc(nhl.primary, nhl.accent)), ("nfl", _disc(primary, alternate))):
            path = root / sport / f"{abbrev}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            art.save(path)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(logos, "LOGO_DIR", root)
        yield root


# -- rendering and comparison ----------------------------------------------------


def render(scene: Scene, size: tuple[int, int]) -> Image.Image:
    ctx = scene.context(*size)
    random.seed(SEED)                       # splash picks its flavour text at random
    scene.board.enter(ctx, scene.cfg)
    random.seed(SEED)
    frame = scene.board.render(ctx, scene.cfg)
    assert frame.size == size, f"{scene.name} drew {frame.size} for a {size} panel"
    return frame.convert("RGB")


def differing_pixels(a: Image.Image, b: Image.Image) -> int:
    mask = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v else 0)
    return mask.histogram()[255]


def write_failure_sheet(path: Path, expected: Image.Image, actual: Image.Image) -> None:
    """expected | actual | diff, side by side and zoomed, so the change is obvious at a glance."""
    diff = ImageChops.difference(expected, actual).convert("L").point(lambda v: 255 if v else 0)
    diff = Image.merge("RGB", (diff, Image.new("L", diff.size, 0), diff))       # magenta where they differ
    gap = 2
    w, h = expected.size
    sheet = Image.new("RGB", (3 * w + 2 * gap, h), (40, 40, 40))
    for i, img in enumerate((expected, actual, diff)):
        sheet.paste(img, (i * (w + gap), 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.resize((sheet.width * ZOOM, sheet.height * ZOOM), Image.NEAREST).save(path)


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_frame_matches_golden(case: tuple[Scene, tuple[int, int]]) -> None:
    scene, size = case
    actual = render(scene, size)
    path = golden_path(scene, size)
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        actual.save(path)
        return
    if not path.is_file():
        pytest.fail(f"no golden for {case_id(case)}; look at the board, then create it with\n  {UPDATE_HINT}")
    expected = Image.open(path).convert("RGB")
    if expected.tobytes() == actual.tobytes():
        return
    sheet = FAILED_DIR / path.relative_to(GOLDEN_DIR)
    write_failure_sheet(sheet, expected, actual)
    changed = differing_pixels(expected, actual)
    pytest.fail(f"{case_id(case)}: {changed} pixel(s) differ from the golden.\n"
                f"  expected | actual | diff: {sheet}\n"
                f"  if the new frame is right: {UPDATE_HINT}")


# -- guards ------------------------------------------------------------------------


def test_every_registered_board_has_a_scene() -> None:
    """A new board without a golden would silently miss the safety net."""
    registered = set(load_registry().boards)
    covered = {scene.name.split("/", 1)[0] for scene in SCENES}
    assert registered - covered == set(), f"boards without a golden scene: {sorted(registered - covered)}"


def test_scene_names_match_their_board_keys() -> None:
    wrong = {scene.name: type(scene.board).key for scene in SCENES if scene.name.split("/", 1)[0] != type(scene.board).key}
    assert wrong == {}, f"scene named after the wrong board: {wrong}"


def test_no_orphaned_goldens() -> None:
    """A renamed or removed scene must take its PNG with it, or the directory rots."""
    expected = {golden_path(scene, size) for scene, size in CASES}
    on_disk = {p for p in GOLDEN_DIR.rglob("*.png") if FAILED_DIR not in p.parents}
    orphans = sorted(str(p.relative_to(GOLDEN_DIR)) for p in on_disk - expected)
    assert orphans == [], f"goldens with no scene: {orphans}"
