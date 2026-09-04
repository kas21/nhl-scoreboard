# Development

## Setup
```bash
uv sync --extra dev --extra emulator
uv run scoreboard --demo --emulator      # emulator window + web UI, replaying a recorded game
uv run pytest -q && uv run ruff check scoreboard tests
```
Python ≥ 3.11 (Pi OS Bookworm ships 3.11, Trixie 3.13). No Node toolchain: the UI is plain ES modules.

## Workflow
- Boards are pinned pixel-for-pixel by `tests/test_golden.py`: every board, in its key states, rendered from
  the fixtures and compared with the PNGs under `tests/golden/`. A failure writes `expected | actual | diff`
  sheets to `tests/golden/_failed/` — look at them, and if the new frame is the one you meant, accept it with
  `SCOREBOARD_UPDATE_GOLDENS=1 uv run pytest tests/test_golden.py` and commit the PNGs with the change.
  New boards must be added to `tests/golden_scenes.py` (a guard test says so). `uv run python tools/golden_sheet.py`
  tiles the goldens into one gallery; readability at 1:1 on LEDs still differs from the emulator, so check there too.
- Every change: tests + ruff must pass; commit with `type: message`; push to `main`.
- Deploy to the Pi: push, then Dashboard → *Update & restart* (or `POST /api/system/update`
  with `X-Requested-With: scoreboard-ui` — see [HARDWARE.md](HARDWARE.md#security)); check `/api/status` and the preview.
- `SCOREBOARD_CONTRACT_TEST=1 uv run pytest tests/test_nhl_contract.py` checks the *live* NHL feed still
  carries every field `nhl/normalize.py` reads. The normal suite only checks that spec against the recorded
  fixtures; run the live pass on a schedule, because the failure it catches is silent — a renamed field
  makes the boards draw a plausible wrong scoreboard rather than crash.
- Playlists on an existing install don't pick up new default entries — add new boards through the
  Boards page or a PATCH to `/api/config`.

## Adding a font / logo
- Fonts: drop a BDF in `render/fonts/bdf/` and run `tools/build_fonts.py`; map sizes in `render/text.py`.
- Team logos aren't in the repo: `logos.py` fetches them from ESPN's CDN on first run into
  `$SCOREBOARD_CACHE_DIR/logos/{sport}/{ABBREV}.png` (default `~/.scoreboard/cache`). To override one, drop a PNG there; to re-fetch, delete it.
- Alternate logos: a team can use a variant instead (`logovariants.py`), cached alongside as
  `{ABBREV}__{variant}.png`. Boards never ask for one — they call `teams.logo(abbrev, size)` as
  always and `logos.logo()` resolves the choice from config, so adding a variant needs no board change.
  The branded variants live on a per-team GUID path that only ESPN's *team API* hands out, so a
  variant fetch costs one extra request per league; the flat `default`/`dark` paths need none.
  That API 403s unknown user agents, hence the explicit `ESPN_API_UA` on the discovery request.
  Tests run against an empty cache (`conftest.py` points `SCOREBOARD_CACHE_DIR` at a temp dir), so boards
  render the placeholder tile — assert on layout, not on club colours.

## Release checklist (when the repo goes public)
1. GitHub Actions: pytest + ruff on push; build `rgbmatrix` wheels for cp311/cp312/cp313 aarch64.
2. ~~installer clone path + OTA button~~ done.
3. pi-gen image.

## Backlog
OTA/installer (needs public repo) · own rgbmatrix wheels · 64x32 design pass · MLB fixtures from real
captures (the shipped ones are generated) · "preview this board" button (override API exists) · per-board "in every rotation" toggle ·
sheen-speed settings on more boards · previous-season LAST game in the off-season · MQTT/webhook publisher.

