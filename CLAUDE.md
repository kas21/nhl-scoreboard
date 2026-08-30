# CLAUDE.md — nhl-scoreboard

Standalone LED-matrix scoreboard for Raspberry Pi. One Python process, one `config.json`,
configured from a browser. NHL first; NFL, weather, flights and holidays are bundled extras.
This is the clean-slate successor to `nhl-led-scoreboard-v2` + the Tape-to-Tape hub (kept only
as reference for board *designs*; the old code is not used).

## Commands

```bash
uv sync --extra dev --extra emulator                 # dev install (emulator + font build tools)
uv run scoreboard --emulator                        # emulator window (:8888) + web UI (:8080)
uv run scoreboard --demo --emulator                 # replay a recorded NHL game (works in the off-season)
uv run scoreboard --output none                     # headless: browser preview only
uv run pytest -q                                    # ~115 tests, <1s
uv run ruff check --fix scoreboard tests            # lint (rules pinned in pyproject)
uv run python tools/build_fonts.py                  # BDF -> .pil bitmap fonts
```

Deploy to the bench Pi: push to `main`, then either click *Update* on the dashboard or
`curl -s -X POST http://nhl-led-scoreboard-office.local:8080/api/system/update` (the Pi's `~/scoreboard` is a
git checkout of this repo; the app fast-forwards, reinstalls if deps changed, restarts). rsync still works for
uncommitted experiments but will make the checkout dirty — `git checkout .` on the Pi before the next update.
Service: `scoreboard.service` (root, `--output hardware`), config at `/etc/scoreboard/config.json`,
web UI http://nhl-led-scoreboard-office.local:8080, logs `journalctl -u scoreboard`.

## Layout

```
scoreboard/
  app.py            wiring: config -> sources (asyncio) -> snapshot -> director -> output; web server
  __main__.py       CLI (--config, --output auto|hardware|emulator|none, --demo)
  config/           pydantic AppConfig (+ plugin models), atomic ConfigStore w/ backups, salvage/migrate, JSON-schema export
  data/             Snapshot store (immutable, versioned), DataSource contract, SourceHealth (per-source fetch stats),
                    EventBus/detectors, MainEventArbiter
  director/         AppState (boot/error/offseason/offday/pregame/live/intermission/postgame), playlists,
                    brightness schedule, board transitions, event interrupts, quarantine, override
  render/           Pillow layout engine (HBox/VBox/Stack/Anchor/Absolute), bitmap + TTF fonts, animated nodes
                    (Marquee/Sheen/Pulse/Blink/Slide/Fade), Sequence (whole-frame timelines), fx helpers, size profiles
  boards/           Board contract + generic boards (clock, splash, blank, test_pattern, season_countdown)
  output/           matrix (rgbmatrix | RGBMatrixEmulator | null), PreviewHub (WebSocket PNG stream)
  web/              FastAPI API + Preact/HTM UI (no build step): dashboard, boards/playlists, settings, wizard, diagnostics
  nhl/              api-web.nhle.com client, normaliser, source, season phase, event detectors, boards (ported old designs)
  nfl/              ESPN site API, normaliser, source, detectors; boards subclass the NHL ones
  extras/           holidays, flights (adsb.lol + adsbdb + airline logos), weather (Open-Meteo) — same plugin contract
  imagecache.py logos.py  runtime image cache ($SCOREBOARD_CACHE_DIR) + team logos fetched from ESPN's CDN
  assets/           fonts under render/fonts, holiday images, penalty gif (team logos are fetched at runtime)
tests/              pytest; fixtures/ are real API captures (NHL 2026-04-11 game day, ESPN, adsb.lol, Open-Meteo)
tools/ scripts/     build steps; Pi install.sh + pi_tuning.sh
docs/               USER_GUIDE, HARDWARE, ARCHITECTURE, DATA, PLUGINS, DEVELOPMENT
```

## Key concepts (read docs/ARCHITECTURE.md for detail)

- **Boards are pure**: `render(ctx, cfg) -> PIL.Image` from an immutable `Snapshot`; `ctx.elapsed` is the
  only clock; no I/O. Boards never fetch — sources do, in the background, on their own cadence.
- **Snapshot keys** (docs/DATA.md): `<sport>.scores|standings|team_summary|season|main_event`, `main_event`
  (arbitrated across sports), `system`, `holidays.upcoming`, `flights.nearby|overhead`, `weather.current|daily`.
- **Events** are derived by diffing consecutive snapshots (goal, penalty, touchdown, flight overhead…);
  event boards pre-empt the playlist, then it resumes. Bursts collapse to the latest per kind/team.
- **Config**: `config.json` stores only overrides; the API returns effective values (model defaults merged).
  Every pydantic field appears in the web UI automatically. Live edits apply without restart, except
  `display.*` driver options (need a restart — the wizard has a button).
- **Plugins**: `scoreboard.boards` / `scoreboard.sources` / `scoreboard.detectors` entry points; bundled
  extras use the same mechanism. A board may declare `sport` and `requires` (snapshot keys, must be non-empty).

## Conventions & gotchas

- Text: bitmap fonts (`pl`, `pixel`, `narrow` families) for anything small; TTF only for big score/clock/headers.
  Measure with the same 1-bit mode you draw in (`text_box`), or glyphs clip.
- Animated nodes cache material by the child's *content hash*; never key caches on `id(image)`.
- Layout: containers stretch on the cross axis and centre children when there are no Spacers.
- Old-design fidelity matters to Kevin: the 128x64 boards are pixel ports of the old Qt client
  (spec captured in the git history of `nhl/boards/game.py`). Keep that look; 64x32 is best-effort.
- Pi panel: `rgb_sequence=RGB`, `slowdown_gpio=2`, `isolcpus=3`, `snd_bcm2835` blacklisted.
- Lint gate: `ruff check` must pass before commit (the CI/commit chains use `&&`; don't pipe through tail).
