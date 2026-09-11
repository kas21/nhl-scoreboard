# nhl-scoreboard

Standalone LED matrix scoreboard for Raspberry Pi. One process, one config
file, configured from a browser — no broker, no SSH. NHL first; NFL, college football, MLB, weather
(with watches and warnings), flights and holiday countdowns are bundled extras.

## Documentation

| Read this | If you want to |
|---|---|
| [How it works, the short version](docs/OVERVIEW.md) | understand the design in five minutes (start here) |
| [User guide](docs/USER_GUIDE.md) | set it up and use it from the web UI |
| [Hardware & install](docs/HARDWARE.md) | wire a panel, install on a Pi, update, secure it |
| [Development](docs/DEVELOPMENT.md) | run it on your machine, test, deploy |
| [Architecture](docs/ARCHITECTURE.md) | threads, data flow, the director's frame loop, caches, config, failure handling |
| [Data model & APIs](docs/DATA.md) | every snapshot key, event and external endpoint |
| [Writing plugins](docs/PLUGINS.md) | add a board, a data source or a sport |

## Getting started

**On a Raspberry Pi with a panel** (see [HARDWARE.md](docs/HARDWARE.md) for parts and wiring):

```bash
curl -fsSL https://raw.githubusercontent.com/kas21/nhl-scoreboard/main/scripts/install.sh | bash
sudo /opt/scoreboard/scripts/pi_tuning.sh && sudo reboot
```

Then open `http://<hostname>.local:8080` and follow the setup wizard.

**On your own machine** (macOS or Linux, Python 3.11+, [uv](https://docs.astral.sh/uv/)):

```bash
uv sync --extra dev --extra emulator
uv run scoreboard --demo --emulator     # replays a recorded game into an emulator window; web UI on :8080
uv run scoreboard --emulator            # live data
uv run scoreboard --output none         # headless: browser preview only
uv run pytest -q                        # ~500 tests in a few seconds
```

Open http://localhost:8080 — the dashboard shows exactly what the matrix shows.

## How it fits together

Background **sources** fetch each league and each extra on their own cadence and publish plain dicts into
an immutable **snapshot**. **Detectors** diff consecutive snapshots into events (goal, touchdown, weather
warning, plane overhead). Thirty times a second the **director** works out the app state from the snapshot
(off-season, off day, pregame, live, intermission, postgame), picks a **board** from that state's playlist
or lets an event board interrupt, and asks it to draw a frame. Boards are pure: snapshot in, image out.
The frame goes to the panel and to a browser preview. Every setting is a pydantic model, so the web UI is
generated from the schema and edits apply on the next frame.

## Layout

```
scoreboard/
  app.py            wiring: config -> sources (asyncio) -> snapshot -> director -> output; web server
  __main__.py       CLI (--config, --output auto|hardware|emulator|none, --demo)
  config/           pydantic AppConfig (+ plugin models), atomic ConfigStore w/ backups, salvage/migrate, JSON-schema export
  data/             Snapshot store, DataSource contract, SourceHealth, EventBus/detectors, MainEventArbiter
  director/         app state, playlists, brightness schedule, board transitions, event interrupts, quarantine, override
  render/           Pillow layout engine, bitmap + TTF fonts, animated nodes, Sequence timelines, size profiles
  boards/           board contract + generic boards (clock, splash, blank, test pattern, season countdown)
  output/           matrix (rgbmatrix | RGBMatrixEmulator | null) and the browser preview hub
  web/              FastAPI API + Preact/HTM UI (no build step): dashboard, boards/playlists, settings, wizard, diagnostics
  nhl/ nfl/ ncaaf/ mlb/   one package per league: API client, normaliser, source, detectors, boards
  extras/           holidays, flights, weather and weather alerts — same plugin contract as the sports
  imagecache.py logos.py  runtime image cache and team logos fetched from ESPN's CDN (no artwork in the repo)
  demo.py           replays tests/fixtures/nhl as a live game
  sim/              the simulator: claim a feed's keys and drive the boards from the browser (nhl/sim.py is the NHL engine)
  plugins.py        entry-point discovery for boards / sources / detectors
tests/              pytest; fixtures/ are real API captures; golden/ pins every board's pixels
tools/ scripts/     font build; Pi install.sh + pi_tuning.sh
docs/               the documents linked above
```

## Animation

Two layers, both pure functions of time:

- **Element-level, continuous** — animated nodes inside any layout tree:
  `Marquee`, `Sheen`, `Pulse`, `Blink`, `Slide`, `Fade`, `Cycle` (`scoreboard/render/animated.py`).
  Pass `t=ctx.elapsed` to `render_tree`. Static subtrees are cached, so per-frame
  cost is proportional to what moves (~0.4 ms for the live board at 128x64).
- **Whole-frame, finite** — `Sequence(fps).flash(...).slide_in(...).hold(6).fade_out(0.5).build(still)`
  for enter/exit transitions; `SequenceMixin` turns a board into `build(ctx, cfg) -> Sequence`.

Sport packages and extras register data sources, boards and event detectors via `scoreboard.*` entry
points; a third-party package does exactly the same.
