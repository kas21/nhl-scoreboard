# How it works — the short version

For developers who want the shape of the thing in five minutes. Everything here is expanded in
[ARCHITECTURE.md](ARCHITECTURE.md) (mechanics), [DATA.md](DATA.md) (what the data looks like) and
[PLUGINS.md](PLUGINS.md) (how to add to it).

## One sentence

Background tasks fetch sports and weather data into an in-memory **snapshot**; thirty times a second a
**director** picks a **board** for the current situation and asks it to draw a frame from that snapshot;
the frame goes to the LED panel and to a browser preview.

## The ELI5

Think of a newsroom with a wall of screens.

- **Reporters** (data sources) each cover one beat — the NHL, the NFL, the weather, planes overhead — and
  phone in whatever they find. They never touch the screens.
- **The wire board** (the snapshot) is where their reports are pinned. It is replaced, never edited: every
  report swaps in a fresh copy of the whole board. Anyone can read it at any time without waiting.
- **A spotter** (the event detectors) compares each new board with the last one and shouts when something
  changed that deserves a reaction: "goal!", "tornado warning!", "plane overhead!".
- **The producer** (the director) decides what is on the screen right now. Normally it runs a rundown
  (a playlist) for the current situation — game night, off day, off-season. When the spotter shouts, it
  cuts to the celebration and then goes back to the rundown.
- **Graphics artists** (boards) each know how to draw one thing — the live score, the standings, the clock,
  a weather warning — given the wire board and how long they have been on screen. They cannot make phone
  calls; they only draw.
- **The panel** shows whatever the producer hands over, and a **browser** mirrors it.
- **The settings page** edits one JSON file. Every setting is a typed model, so the form draws itself
  from the schema, and edits apply on the next frame without a restart.

## A frame's journey

```
 asyncio thread                                     render thread (30 fps)
 ┌──────────────┐  publish(key, value)   ┌────────┐   frame()    ┌──────────┐  show()   ┌────────┐
 │ NHL source   │ ─────────────────────▶ │Snapshot│ ───────────▶ │ Director │ ────────▶ │ matrix │
 │ NFL source   │                        │ store  │              │          │           └────────┘
 │ weather …    │                        └───┬────┘              │ state    │  submit() ┌────────┐
 └──────────────┘                            │ listeners         │ playlist │ ────────▶ │preview │
                                             ▼                   │ events   │           └────────┘
                                    EventBus (detectors)  ─────▶ │ boards   │
                                    MainEventArbiter      ─────▶ └──────────┘
```

1. A source wakes up on its own timer, calls its API through `ctx.http`, normalises the answer into plain
   dicts and lists, and calls `ctx.publish()`.
2. The snapshot store makes a new immutable `Snapshot` with that key replaced, then tells its listeners.
   The event bus runs every detector on (old, new) and queues what they return. The arbiter picks the
   app-wide `main_event` from each sport's candidate.
3. On the render thread, `Director.frame()` drains the event queue, computes the app **state** from the
   snapshot (boot / error / offseason / offday / pregame / live / intermission / postgame), and selects a
   board: a forced override from the UI, else the event board for a queued event, else the next entry of
   the state's playlist whose data is present.
4. If the board changed (or the same event board got a new event), it is `enter()`ed once; then
   `render(ctx, cfg)` returns a PIL image. A raising board is quarantined for a minute.
5. The frame is blended with the previous one if a transition is in progress, gets a red dot if the feed
   is offline, and goes to the output driver and the preview hub.

## What happens when your team scores

1. The NHL source polls the score endpoint every 5 s during a live game and publishes `nhl.scores` and
   `nhl.main_event` (held back by `delay_seconds` if you are matching a TV feed).
2. The NHL detector sees the home score go from 2 to 3 between two snapshots and queues an `nhl.goal`
   event with the scorer and assists.
3. Next frame, the director pops the event, finds the goal board (`event_kinds` contains `nhl.goal`,
   `matches()` says yes), enters it — the board pre-renders its whole timeline as a `Sequence` — and
   plays it with no transition in.
4. When the sequence reports `done()`, the director returns to the live playlist, which is the game board,
   with a transition out. Bursts (a restart mid-game, missed polls) collapse to one event per kind and team.

## Vocabulary

| Word | Meaning | Where |
|---|---|---|
| Source | Background fetcher; publishes JSON-shaped values under keys | `data/source.py`, `nhl/source.py`, `extras/*/source.py` |
| Snapshot | Immutable, versioned dict of everything published, with per-key timestamps | `data/store.py` |
| Detector | Pure function `(prev, new) -> events` | `nhl/events.py`, `extras/.../source.py` |
| Event | `kind`, `team`, `payload`, `ts`; interrupts the playlist | `data/events.py` |
| Arbiter | Chooses `main_event` across sports: any live game first, then `sports.priority` | `data/arbiter.py` |
| State | boot / error / offseason / offday / pregame / live / intermission / postgame | `director/state.py` |
| Playlist | Ordered board entries per state, from config | `config/models.py`, `director/playlist.py` |
| Board | Pure renderer: `render(ctx, cfg) -> Image`; optional `enter`, `done`, `auto_seconds` | `boards/base.py` |
| Event board | Board that plays for an event; never in a playlist (`playlistable = False`) | `boards/base.py` |
| Sequence | Pre-rendered finite frame list for whole-frame animation | `render/anim.py` |
| Layout tree | `HBox` / `VBox` / `Text` / `Img` … measured and placed by Pillow | `render/layout.py` |
| Animated node | `Marquee`, `Pulse`, `Sheen`… — a function of `t` inside a static tree | `render/animated.py` |
| Size profile | Font and logo sizes for a panel size (128x64, 64x32…) | `render/profiles.py` |
| Registry | Boards, sources and detectors discovered from entry points | `plugins.py` |
| Config store | Atomic `config.json` with backups, salvage and migrations; listeners fire on change | `config/store.py` |
| Source health | Per-source fetch, publish and crash stats for the diagnostics page | `data/health.py` |

## Rules that everything else depends on

- **Boards never fetch and never read the wall clock.** `ctx.now` and `ctx.elapsed` are the only time; the
  snapshot is the only data. This is what makes every board testable from fixtures and pinned by golden PNGs.
- **Sources never draw.** They may cache on disk (logos, airline art) but boards only read what has landed.
- **Everything shared between threads is immutable and swapped, not mutated**: snapshots, config models,
  source stats, the director's board-config cache. Readers never lock.
- **Config stores overrides only.** Defaults live in the pydantic models; the API returns effective values.
- **Failure is contained per part.** A crashing source restarts with backoff; a raising board is quarantined;
  an unreachable feed keeps the last data and shows a dot; a broken config loses only its bad keys; a dead
  render thread exits the process so systemd restarts it.

## Running it

```bash
uv sync --extra dev --extra emulator
uv run scoreboard --demo --emulator     # replays a recorded game into an emulator window; UI on :8080
uv run pytest -q                        # ~500 tests, ~6 s; goldens pin every board's pixels
```

Then open http://localhost:8080. The dashboard shows the panel, the Boards page the playlists, Diagnostics
the sources and their next poll. Change a setting and watch the next frame pick it up.

## Where to look next

- Adding a board or a source: [PLUGINS.md](PLUGINS.md), then copy `extras/weather/` or `nfl/`.
- What a key in the snapshot holds: [DATA.md](DATA.md).
- Threads, caches, failure paths, the director's frame loop in detail: [ARCHITECTURE.md](ARCHITECTURE.md).
- Wiring a panel and installing on a Pi: [HARDWARE.md](HARDWARE.md). Using it: [USER_GUIDE.md](USER_GUIDE.md).
- Day-to-day workflow, goldens, deploys: [DEVELOPMENT.md](DEVELOPMENT.md).
