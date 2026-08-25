# nhl-scoreboard

Standalone LED matrix scoreboard for Raspberry Pi. One process, one config
file, configured from a browser — no broker, no SSH.

## Develop

```bash
uv sync --extra dev --extra emulator
uv run scoreboard --emulator            # emulator window + web UI on :8080
uv run scoreboard --output none         # headless: browser preview only
uv run scoreboard --demo --emulator     # replay a recorded game (no network, works in the off-season)
uv run pytest
uv run --extra build python tools/rasterize_logos.py   # regenerate logo PNGs from SVGs
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

Boards are pure: `render(ctx, cfg) -> PIL.Image`. Sport packages register
data sources, boards and event detectors via `scoreboard.*` entry points.
