# Architecture

Start with [OVERVIEW.md](OVERVIEW.md) if you have not read it: this page is the mechanics behind that picture.

```
 sources (asyncio tasks)          store              director (render thread, 30 fps)          output
 nhl / nfl / ncaaf / mlb  ──▶  Snapshot (immutable, ──▶  state ← main_event/season          ──▶ matrix
 holidays / flights / weather   versioned dict)          playlist cursor, transitions,           preview ws
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
4. **Shared state is swapped, never mutated.** Snapshots, config models, source stats and the director's
   board-config cache are frozen objects replaced whole, so a reader on another thread sees either the old
   or the new one and never locks.
5. **Appliance robustness**: offline only after 3 consecutive failures (stale dot, keep last data); boards that
   raise are quarantined 60 s; render-thread death exits the process (systemd restarts); config salvage
   drops only bad keys.

## Process model: threads and loops
`app.py` builds everything in `Application.__init__` and runs two worlds side by side:

| Thread | Runs | Paced by |
|---|---|---|
| main (asyncio) | one task per source (`run_source_forever`), uvicorn for the web UI, a render-thread watchdog (1 s), the GitHub update checker (`web.update_check_hours`), signal handling | event loop |
| `render` | `Director.frame()` → `output.show()` → `preview.submit()`; brightness applied each frame | `display.fps` (default 30), sleeps the rest of the budget |
| `preview-encode` | PNG-encodes the latest submitted frame for browsers | frames arrive; drops when busy |
| matrix driver (C++) | `rgbmatrix`'s own refresh thread, pinned to the isolated core on a Pi | hardware |

Hand-offs between them are all one-way and lock-light: sources publish into the snapshot store (a lock
around the swap only; listeners run outside it), the event bus queue is locked for the append/drain
handover, the preview hub takes a frame copy through a one-slot queue, and config listeners run on
whichever thread saved the config (the web thread, usually) and only ever replace references.

Exit codes: a render loop that fails 300 frames in a row (about 10 s) exits with 4; a render thread that
dies exits with 3; a restart requested from the UI (driver options changed, or an update) exits 0. Under
systemd all three come back within seconds.

`scoreboard --output auto` tries the real driver, then the emulator, then a null sink; `--demo` replaces the
NHL source with `demo.py`, which replays `tests/fixtures/nhl` as a live game so every state and interrupt
can be exercised offline.

## Data flow, end to end

### 1. Sources publish
A source is a class with `key`, `config_model` and `async run(ctx)`. `SourceContext` is everything it may
touch: `ctx.http` (a shared `httpx.AsyncClient`, wrapped so each request's outcome and latency are
attributed to the source), `ctx.config` (its section of the config, re-validated on every read so UI edits
apply on the next loop and an invalid section falls back to defaults), `ctx.timezone` and `ctx.location`
(kept current by a config listener), `ctx.publish(value, subkey)` → `<key>.<subkey>`, `ctx.publish_to(key,
value)` for shared keys such as `main_event` and `system`, `ctx.sleep(seconds)` (records the next poll for
the diagnostics page), `ctx.snapshot()` and `ctx.drift(note)` — the feed no longer looks the way the source
expects (an unknown enum value, a missing field, a team the registry has never heard of). A forgiving
normaliser turns a renamed field into a plausible wrong board rather than a crash, so this is the signal
that its best guess may be wrong: logged once per distinct note, counted in health, listed on the
diagnostics page. The NHL source checks every payload against `nhl/contract.py`, the same spec the test
suite runs against the fixtures and a weekly GitHub Action runs against the live API.

`run_source_forever` supervises it: a crash is logged, counted in health, and the source is restarted after
2, 5, 15, 30 then 60 s; cancellation at shutdown propagates. A source that *returns* is restarted too.

Cadences are the source's own business, driven by its config and by what it sees: the NHL source polls
scores every `live_interval` (5 s) while a favourite's game is active and `idle_interval` (60 s) otherwise,
and refreshes standings, team summaries, season dates and the schedule hourly in a second loop; the weather
alerts source polls every `poll_seconds` but naps only until the soonest alert lapses so it can retire it
on time. [DATA.md](DATA.md#external-apis-all-keyless) lists every endpoint and cadence.

Only the NHL source publishes `system.online`: three consecutive score-poll failures set it false, one
success sets it true. That is what draws the stale dot and, with no data at all, puts the app in ERROR.

### 2. The snapshot store
`Snapshot` is a frozen dataclass: `version` (monotonic), `data` (key → value, a read-only mapping) and
`updated` (key → epoch seconds of the last publish). `SnapshotStore.publish(key, value)` builds a new
snapshot with that one key replaced, swaps it in under a lock, then calls every listener with `(prev, new)`
outside the lock. Values are plain dicts and lists by convention; `None` is a legitimate value (the weather
alerts source uses it for "unknown", the arbiter for "no game"). `snapshot.get(key)` returns `None` for
both a missing key and a `None` value; `snapshot.has(key)` tells them apart.

A key can be **claimed** (`store.claim(keys, owner)`): while an owner holds it, a publish from anyone else
is *shadowed* — kept aside, not applied, no listener run — and `release()` publishes the last shadowed value,
or failing that the value from before the claim, so readers are back on real data at once. Every publish
through a `SourceContext` carries the source's key as its owner. Only the simulator claims anything today
(see [Simulation](#simulation)); the mechanism is what lets it take a feed over without stopping the source.

Two listeners are wired at startup:

- **`EventBus.on_snapshot`** runs every registered detector on `(prev, new)` and appends what they return
  to a queue. Detectors are pure diffs (`nhl/events.py`, `nfl/events.py`, `mlb/events.py`,
  `extras/flights`, `extras/weather/alerts`) and run outside the lock so a slow plugin cannot stall a
  publish. The director drains the queue once per frame; a drain collapses bursts to the latest event per
  `(kind, team)`, so a restart mid-game plays one goal, not nine.
- **`MainEventArbiter.on_snapshot`** watches every `<sport>.main_event` key and republishes the winner as
  `main_event`: any live or intermission game first (by `sports.priority`), else the first sport with a
  game today, else `None`. Boards and the state machine only ever read `main_event`, so the director is
  sport-agnostic.

### 3. The director picks a board
`Director.frame(mono)` on the render thread, every frame:

1. **Drain events** into a pending list.
2. **Sync state**: `compute_state(snapshot)` — ERROR when offline with nothing ever published, else the
   `main_event.phase` (pregame / live / intermission / postgame), else OFFSEASON if every `<sport>.season`
   says so, else OFFDAY; BOOT for the first 4 s. A state change resets the playlist cursor to the first
   entry and restarts its clock.
3. **Select**, in priority order: a UI override (`POST /api/override`, time-limited); the event board
   already playing; the first pending event some event board `matches()` (it becomes the active event);
   the pinned splash (BOOT) or clock (ERROR); else the playlist for the state. Playlist entries are
   filtered each frame: enabled, board loaded, not quarantined, `playlistable`, every `requires` key
   present and non-empty, and `sport` matching `main_event.sport` for sport-specific boards. If nothing
   survives, the clock.
4. **Enter** the board when it changed — or when the same event board has a new event behind it, so a
   cached `Sequence` is rebuilt for the new payload. Playlist boards get a transition in from the last
   frame (`config.transition`: fade / slide / wipe / blinds); event boards cut in instantly.
5. **Render** with a `BoardContext` (snapshot, size profile, width, height, fps, `now` in the configured
   zone, `elapsed` since enter, the event). A board that raises is quarantined for 60 s and the cursor moves
   on; the last good frame is shown meanwhile.
6. **Post**: blend the transition, add the stale dot when `system.online` is false, then decide what is
   next: an event board ends when `done()` or after 30 s; a playlist entry ends when its `duration`
   expires or, with `duration: null` ("auto"), when the board says `done()`. `auto_seconds()` reports that
   same length to the web UI.

Per-board config is validated from `config.boards[key]` once per config version and cached in a map the
config listener replaces (never clears), so the render thread cannot observe a half-cleared cache.

### 4. Output
`Output` is a three-method protocol: `show(frame)`, `set_brightness(percent)`, `close()`. `MatrixOutput`
wraps `rgbmatrix` (hardware) or `RGBMatrixEmulator` (same API) with double-buffered `SwapOnVSync`;
`NullOutput` keeps the last frame for tests and headless runs. Brightness comes from
`director.brightness()` each frame: fixed, sunrise/sunset via `astral`, or a nightly window, with
`keep_bright_when_live`.

`PreviewHub` mirrors frames to browsers: the render thread copies a frame into a one-slot queue at most
`web.preview_fps` times a second (once a second when nobody is watching, so `/api/preview.png` stays
fresh); an encoder thread turns it into PNG bytes and fans them out to `/ws/preview` subscribers, each of
which has a two-deep queue that drops the oldest frame when a client falls behind.

## Simulation
`sim/` drives the boards by hand from the browser without a mock anywhere: a **simulation** (`sim/base.py`,
`Simulation` protocol) claims the snapshot keys a source would publish and writes real-shaped values there,
so the arbiter, the detectors, the state machine and every board run exactly as on a game night. `SimulatorHub`
(`sim/hub.py`, one per app) holds the running engines, ticks them four times a second from an asyncio task
(`Application.run_async`), publishes when an engine reports a change, and serialises every call under one lock
because the web API drives it from worker threads. Several engines may run at once as long as their claims do
not overlap; a conflicting start is refused whole (`ClaimError` → 409). A start that fails releases its
claims; an engine that raises in `tick()` is stopped, not left holding the feed.

An engine is a plain object: `options_model` (pydantic; its JSON schema is the start form),
`claims`, `start(options, ctx)`, `tick(ctx) -> changed`, `action(name, params, ctx)`, `actions()` (the
buttons that apply *right now*, as `Action`/`Param` specs the page draws), `values()` (key → value to
publish) and `describe()` (a headline and label/value lines). `SimContext` gives it a monotonic `now`, the
local wall clock, the snapshot (still real for the claimed keys at `start`, so an engine can take the
records and the rest of the slate from it) and the app config. Registration is the `scoreboard.sims`
entry-point group; `nhl/sim.py` is the bundled engine — a hockey game with a running clock, goals, penalties
with a timed power play, pulled goalies, intermissions, overtime and a shootout, publishing
`nhl.main_event` and `nhl.scores` in `normalize.py`'s shape (plus `simulated: true`).

Routes: `GET /api/sim` (every engine's form schema, and for a running one its options, summary and
actions), `POST /api/sim/{key}/start|stop|action`, `POST /api/sim/stop`. `/api/status` lists what is
`simulating`, and the UI shows a badge on every page while anything is, with a stop button on it. Nothing is
persisted; a restart ends every simulation. `--demo` is the older, scripted cousin: it *replaces* the NHL
source with a fixture replay and needs a restart to leave.

## Render engine (`render/`)
- **Layout tree**: `Text`, `Img`, `Box`, `Spacer` leaves; `HBox`, `VBox`, `Stack`, `Anchor`, `Absolute`
  containers. Nodes measure their natural size, containers assign integer rects, `render_tree(root, w, h, t)`
  composites into an RGB frame. Containers stretch on the cross axis and centre children when there are no
  `Spacer`s.
- **Static subtree cache**: a process-wide LRU of 512 composited images keyed by the subtree's structure
  and size (`cache_key()`), so a frame costs roughly what moves in it.
- **Animated nodes** (`Marquee`, `Sheen`, `Pulse`, `Blink`, `Slide`, `Fade`, `Cycle`) pre-render their
  child's material once, cached by the child's *content* hash (never by object identity), and do one crop
  or composite per frame from `t`. Sub-pixel sheen via a sheared profile.
- **Sequences**: `Sequence(fps).flash().slide_in().hold().fade_out().build(still)` compiles to a frame list
  once; `SequenceMixin` turns a board into `build(ctx, cfg) -> Sequence` and handles caching, rebuild on
  size change, playback and `done`. Goal, penalty, splash and the interrupt boards are sequences; the cost
  is one synchronous pre-render on enter (tens of milliseconds on a Mac, a fraction of a second on a Pi)
  and the frames stay referenced until the next build.
- **Fonts**: X11 bitmap BDFs converted to `.pil` for small text (crisp on LEDs), TTF for big scores and
  clocks; `load_font` is cached. Measure with `text_box` in the same 1-bit mode you draw in, or glyphs clip.
- **Size profiles** (`profiles.py`) give each panel size its font, logo and padding sizes; unknown sizes
  snap to the nearest smaller profile. The 128x64 boards are pixel ports of the old Qt client; 64x32 is
  best-effort.
- Cost: ~0.4 ms/frame for the live board at 128x64 on a Mac; the Pi 4 runs ~1 core for the driver thread.

## Caches and on-disk state

| What | Where | Keyed by / lifetime |
|---|---|---|
| Static subtrees, animated material | in-process LRU (`render/layout.py`, 512 entries) | structure + size / content hash |
| Fonts, fx tiles, sheen ramps | `functools.lru_cache` | name + size |
| Decoded images (logos, holiday art) | `imagecache._decode`, 256 entries | path + size + file mtime, so a file that lands after a miss is picked up |
| Board configs | `Director._board_cfg_cache` | (board key, config version); replaced on config change |
| Built sequences | the board instance (`SequenceMixin._seq`) | until re-entered or the panel size changes |
| Team logos | `$SCOREBOARD_CACHE_DIR/logos/{sport}/{ABBREV}.png` (+ `__variant`) | fetched once from ESPN's CDN by the sport source on startup and when the logo config changes; boards only read; delete to re-fetch, drop a PNG to override |
| Airline logos | `$SCOREBOARD_CACHE_DIR/airline-logos/` | once per operator code, misses retried weekly |
| Callsign → route/airline | flights source, in memory | 6 h positive, 1 h negative |
| Last alerts fetch | weather alerts source, in memory | re-filtered between polls |
| Latest preview PNG | `PreviewHub` | replaced per encode |
| Airframe sighting log | `$SCOREBOARD_DATA_DIR/flights/sightings.json` | user data, never re-downloadable |
| Uploaded holiday pictures | `$SCOREBOARD_DATA_DIR/holidays/<slug>.png` | user data |
| Config | `config.json` (+ `.1`…`.5` backups, `.broken`, `.tmp`) | see below |

`SCOREBOARD_CACHE_DIR` defaults to `~/.scoreboard/cache` (`/var/cache/scoreboard` under systemd) and is
safe to clear; `SCOREBOARD_DATA_DIR` (`~/.scoreboard/data`, `/var/lib/scoreboard`) holds what the user
supplied. Both live outside the checkout so an OTA update cannot delete them.

## Config lifecycle
`config.json` holds **overrides only**; every default is a pydantic field in `config/models.py`, and the
API returns effective values. `ConfigStore.update(patch)` deep-merges, validates the whole `AppConfig`,
writes atomically (temp file + rename, mode 0600, five rotating backups) and then calls its listeners with
the new model; nothing is written if validation fails. On load a document that is not JSON is moved to
`config.json.broken` and defaults are used; an old `version` is migrated step by step (`MIGRATIONS`); a
document with bad keys is *salvaged* — only the offending paths are dropped, and a warning names them.

Listeners registered at startup: log level, logo variant preferences, preview fps, each source context's
timezone and location, the director (board-config cache reset, interrupt-board warning when playlists
change), and the holidays source (republishes immediately when its settings change). Everything applies
without a restart except `display.*` driver options, which need the restart button the wizard offers.

Plugin sections (`boards.<key>`, `sources.<key>`) are free-form dicts in `AppConfig` and are validated
against the plugin's own model when read, so a plugin's bad setting degrades that plugin to defaults rather
than rejecting the whole file.

## Failure handling, by layer
| Failure | Effect |
|---|---|
| API request fails | counted per source; `degraded` after 1, `offline` after 3 consecutive; the source keeps its last data and usually re-filters it (alerts retire, games age) |
| Source crashes | restarted with backoff (2 → 60 s); shows as `crashed` between attempts |
| NHL feed unreachable | `system.online: false` → stale dot; ERROR state only if nothing was ever published |
| Board raises | quarantined 60 s, cursor moves on, last good frame shown; the render loop absorbs it |
| 300 bad frames in a row / render thread dies | process exits (4 / 3) and systemd restarts it |
| Bad config document | broken file set aside; bad keys dropped with a warning; backups kept |
| Bad plugin | its entry point is skipped at load and logged; the rest of the app runs (the director draws black if even the fallback board is missing) |

## Web
FastAPI on `web.port` (8080). Endpoints: `/api/config` (GET effective, PATCH deep-merge, PUT, reset),
`/api/schema`, `/api/status`, `/api/sources` (per-source health), `/api/boards` (key, title, requires,
`playlistable`, `self_timed`, `auto_seconds`), `/api/snapshot`, `/api/logs`, `/api/override` (force a board),
`/api/system` (+ `/restart`, `/hostname`, `/update`, `/update/check`), `/api/geocode`, `/api/preview.png`,
`/ws/preview` (PNG frames), `/api/holidays/images/{slug}` (GET the picture, POST your own as the raw body,
DELETE to put the bundled one back) and `/api/holidays/settings` (GET / PUT), and `/api/sim` (see [Simulation](#simulation)). Those are the only
plugin-specific routes, and each earns it: a picture is a file, so it cannot ride on `/api/config`; and
`PATCH /api/config` deep-merges, so it can add a key to the `overrides` map but never take one out, and
plugin sections are `dict[str, Any]` in `AppConfig` so nothing validates them on the way in. The `/settings`
route validates against `HolidaysConfig` and replaces the section outright. A model can declare a page of
its own with `edited_on()` (see `config/models.py`); the generated settings form then links to it.

State-changing calls need `X-Requested-With: scoreboard-ui` and a `Host` the box answers to
(`web/guard.py`; see [HARDWARE.md](HARDWARE.md#security)). The UI is Preact + HTM served as static files
(no build step): `app.js` (shell, boards, playlists), `dashboard.js`, `settings.js` (schema-driven forms),
`holidays.js`, `sim.js` (the Simulator page: start forms from each engine's schema, buttons from its action
specs — nothing in it knows hockey), `wizard.js` (first-run flow).

## Plugins
`plugins.load_registry()` reads four entry-point groups — `scoreboard.boards`, `scoreboard.sources`,
`scoreboard.detectors`, `scoreboard.sims` — and instantiates each; a broken one is logged and skipped. The bundled sports
and extras register the same way as a third-party package would (see `pyproject.toml`), which is why the
director and the web UI have no sport-specific code. [PLUGINS.md](PLUGINS.md) has the contracts.
