# Development

## Setup
Requirements: Python ≥ 3.11 (Pi OS Bookworm ships 3.11, Trixie 3.13) and [uv](https://docs.astral.sh/uv/).
No Node toolchain: the UI is plain ES modules served as static files. No API keys: every feed is keyless.

```bash
git clone https://github.com/kas21/nhl-scoreboard && cd nhl-scoreboard
uv sync --extra dev --extra emulator     # venv + deps; `emulator` pulls RGBMatrixEmulator, `dev` pytest/ruff/respx
uv run scoreboard --demo --emulator      # emulator window + web UI on :8080, replaying a recorded game
uv run scoreboard --emulator             # …then open Simulator in the UI to run a game by hand (goals, penalties, periods)
uv run scoreboard --emulator             # the same against the live feeds
uv run scoreboard --output none          # headless: browser preview only (CI, SSH sessions)
uv run pytest -q && uv run ruff check scoreboard tests
```

What you get on first run: `~/.scoreboard/config.json` is written with defaults (`--config` to put it
elsewhere), team logos download from ESPN's CDN into `~/.scoreboard/cache/logos/` over the first few
seconds (a coloured tile stands in until then), and the setup wizard opens at http://localhost:8080. Set a
location there or in Settings to turn on weather, alerts, flights and sunset dimming. The emulator window
is the panel; the dashboard's preview is the same frames over a WebSocket.

Useful switches: `-v` for debug logging; `SCOREBOARD_CACHE_DIR` / `SCOREBOARD_DATA_DIR` to relocate the
cache and user data (see [ARCHITECTURE.md](ARCHITECTURE.md#caches-and-on-disk-state)).

## Repo tour
Read [OVERVIEW.md](OVERVIEW.md) first. Then: `app.py` is the wiring, `director/director.py` the frame loop,
`data/` the store and event bus, `boards/base.py` the board contract, `render/layout.py` the layout engine,
`nhl/` the reference sport package and `extras/weather/alerts/` a compact example of a source, a detector,
a playlist board and an interrupt board together. `tests/golden_scenes.py` lists every board and the
snapshot it is rendered from, which doubles as a catalogue.

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
  carries every field `nhl/normalize.py` reads. The spec lives in `nhl/contract.py`; the normal suite checks
  it against the recorded fixtures, the source checks every payload at runtime (mismatches show as *drift* on
  the diagnostics page) and `.github/workflows/nhl-contract.yml` runs the live pass weekly (or from the
  Actions tab). The failure it catches is silent — a renamed field makes the boards draw a plausible wrong
  scoreboard rather than crash — so keep all three in step when you read a new field.
- To see a board react without a game on, use the **Simulator** page (any output mode): it publishes a
  real-shaped game under the NHL keys and every state, interrupt and playlist follows. `--demo` is the
  scripted equivalent for a quick unattended run-through. Both work offline.
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

