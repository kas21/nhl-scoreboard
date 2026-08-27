# Development

## Setup
```bash
uv sync --extra dev --extra emulator
uv run scoreboard --demo --emulator      # emulator window + web UI, replaying a recorded game
uv run pytest -q && uv run ruff check scoreboard tests
```
Python ≥ 3.11 (Pi OS Bookworm ships 3.11, Trixie 3.13). No Node toolchain: the UI is plain ES modules.

## Workflow
- Boards: render to PNG from fixtures and *look at them* (a contact-sheet script is quick to write with
  `BoardContext` + `SnapshotStore`); readability at 1:1 on LEDs differs from the emulator.
- Every change: tests + ruff must pass; commit with `type: message`; push to `main`.
- Deploy to the Pi: push, then Dashboard → *Update & restart* (or `POST /api/system/update`); check `/api/status` and the preview.
- Playlists on an existing install don't pick up new default entries — add new boards through the
  Boards page or a PATCH to `/api/config`.

## Adding a font / logo
- Fonts: drop a BDF in `render/fonts/bdf/` and run `tools/build_fonts.py`; map sizes in `render/text.py`.
- Team logos aren't in the repo: `logos.py` fetches them from ESPN's CDN on first run into
  `$SCOREBOARD_CACHE_DIR/logos/{sport}/{ABBREV}.png` (default `~/.scoreboard/cache`). To override one, drop a PNG there; to re-fetch, delete it.
  Tests run against an empty cache (`conftest.py` points `SCOREBOARD_CACHE_DIR` at a temp dir), so boards
  render the placeholder tile — assert on layout, not on club colours.

## Release checklist (when the repo goes public)
1. GitHub Actions: pytest + ruff on push; build `rgbmatrix` wheels for cp311/cp312/cp313 aarch64.
2. ~~installer clone path + OTA button~~ done.
3. pi-gen image.

## Backlog
OTA/installer (needs public repo) · own rgbmatrix wheels · 64x32 design pass · MLB (reuse the NFL
pattern) · "preview this board" button (override API exists) · per-board "in every rotation" toggle ·
sheen-speed settings on more boards · previous-season LAST game in the off-season · MQTT/webhook publisher.

