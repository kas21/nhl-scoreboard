# Architecture

```
 sources (asyncio tasks)          store              director (render thread, 30 fps)          output
 nhl / nfl / holidays /   ──▶  Snapshot (immutable, ──▶  state ← main_event/season          ──▶ matrix
 flights / weather              versioned dict)          playlist cursor, transitions,           preview ws
        │                           │                    event interrupts, brightness
        │                     EventBus: detectors(prev,new) ─▶ events queue ─▶ event boards
        └── MainEventArbiter: <sport>.main_event ─▶ main_event (live first, then sports.priority)
 config.json ⇄ ConfigStore ⇄ FastAPI (/api/config, schema, status, sources, override, system) ⇄ Preact UI
        └── SourceHealth: per-source fetch/publish/crash stats (fed by ctx.http, ctx.publish, the runner)
```

## Principles
1. **Boards are pure functions** `render(ctx, cfg) -> Image`. `ctx` = snapshot, size profile, wall clock,
   `elapsed`, optional event. No network, no wall-clock reads, no matrix access. Testable with fixtures.
2. **Sources are the only fetchers.** Each `DataSource.run(ctx)` loops forever on its own cadence and
   `ctx.publish()`es JSON-shaped dicts. A slow API never stalls the screen; the render thread reads the
   latest snapshot lock-free. Every request through `ctx.http`, every `ctx.publish()` and every crash/restart
   is recorded in `SourceHealth` (`data/health.py`) and shown on the dashboard/diagnostics pages — plugins get
   this for free; use `await ctx.sleep(s)` instead of `asyncio.sleep` so the UI can show the next poll time.
3. **Schema is the UI.** All settings are pydantic models; `/api/schema` drives the forms.
4. **Appliance robustness**: offline only after 3 consecutive failures (stale dot, keep last data); boards that
   raise are quarantined 60 s; render-thread death exits the process (systemd restarts); config salvage
   drops only bad keys.

## Director state machine
`compute_state(snapshot)`:
- no data at all & offline → **ERROR** (clock)
- `main_event` present → phase: pregame / live / intermission / postgame
- else → **OFFSEASON** if every `<sport>.season` says offseason, otherwise **OFFDAY**
- BOOT for the first 4 s (splash).
Per-state playlist (`config.playlists`). Entry is skipped if the board isn't loaded, is quarantined,
its `requires` keys are missing/empty, or its `sport` ≠ `main_event.sport`. Events pre-empt (no transition
in, transition out). `duration=None` → the board's `done()` decides. Board clock restarts on every switch.
Transitions: fade/slide/wipe/blinds between playlist boards (`config.transition`).

## Render engine (`render/`)
- Layout nodes measure → place; containers cache composited static subtrees in an LRU keyed by structure+size.
- Animated nodes (`Marquee`, `Sheen`, `Pulse`, `Blink`, `Slide`, `Fade`) pre-render material per child and do one
  crop/composite per frame; `t` threads through `render_tree(..., t=)`. Sub-pixel sheen via sheared profile.
- `Sequence(fps).flash().slide_in().hold().fade_out().build(still)` for finite whole-frame timelines;
  `SequenceMixin` for boards that are one timeline (goal, penalty, splash…).
- Fonts: X11 bitmap BDFs converted to `.pil` (small text, crisp), TTF for large. Size profiles per panel size.
- Cost: ~0.4 ms/frame for the live board at 128x64 on a Mac; the Pi 4 runs ~1 core for the driver thread.

## Web
FastAPI on `web.port` (8080). Endpoints: `/api/config` (GET effective, PATCH deep-merge, PUT, reset),
`/api/schema`, `/api/status`, `/api/sources` (per-source health), `/api/boards`, `/api/snapshot`, `/api/logs`, `/api/override` (force a board),
`/api/system` (+ `/restart`, `/hostname`), `/api/geocode`, `/api/preview.png`, `/ws/preview` (PNG frames ~10 fps).
UI is Preact + HTM served as static files (no build step); `wizard.js` is the first-run flow.

## Process model
`app.py`: render loop in a thread (`Director.frame()` → output → preview); asyncio loop runs sources,
uvicorn, a render-thread watchdog and signal handling. Sources get a `SourceContext` (http client,
live config getter, `timezone`, `location`, `publish`).
