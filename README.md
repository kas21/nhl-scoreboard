# nhl-scoreboard

Standalone LED matrix scoreboard for Raspberry Pi. One process, one config
file, configured from a browser — no broker, no SSH. NHL first; NFL, weather,
flights and holiday countdowns are bundled extras.

Docs: [User guide](docs/USER_GUIDE.md) · [Hardware & install](docs/HARDWARE.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Data model & APIs](docs/DATA.md) ·
[Writing plugins](docs/PLUGINS.md) · [Development](docs/DEVELOPMENT.md)

## Develop

```bash
uv sync --extra dev --extra emulator
uv run scoreboard --emulator            # emulator window + web UI on :8080
uv run scoreboard --output none         # headless: browser preview only
uv run scoreboard --demo --emulator     # replay a recorded game (no network, works in the off-season)
uv run pytest
```

Open http://localhost:8080 — the dashboard shows exactly what the matrix shows.

## Layout

```
scoreboard/
  config/    pydantic models + atomic config.json store + JSON-schema export
  data/      immutable Snapshot store, DataSource contract, event detection
  director/  app state, playlists, brightness schedule, board selection
  render/    Pillow layout engine, fonts, size profiles, animation helpers
  boards/    board contract + built-ins (clock, splash, blank)
  nhl/       NHL: api client, normaliser, source, event detectors, boards (game, ticker, standings, team summary, goal, penalty)
  demo.py    replays tests/fixtures/nhl as a live game
  output/    matrix (hardware | emulator | none) and browser preview
  web/       FastAPI API + schema-driven Preact UI (no build step)
  plugins.py entry-point discovery for boards / sources / detectors
```

Boards are pure: `render(ctx, cfg) -> PIL.Image`, with `ctx.elapsed` as the only clock.

## Animation

Two layers, both pure functions of time:

- **Element-level, continuous** — animated nodes inside any layout tree:
  `Marquee`, `Sheen`, `Pulse`, `Blink`, `Slide`, `Fade` (`scoreboard/render/animated.py`).
  Pass `t=ctx.elapsed` to `render_tree`. Static subtrees are cached, so per-frame
  cost is proportional to what moves (~0.4 ms for the live board at 128x64).
- **Whole-frame, finite** — `Sequence(fps).flash(...).slide_in(...).hold(6).fade_out(0.5).build(still)`
  for enter/exit transitions; `SequenceMixin` turns a board into `build(ctx, cfg) -> Sequence`. Sport packages register
data sources, boards and event detectors via `scoreboard.*` entry points.
