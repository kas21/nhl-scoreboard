# Writing a board or data source

Everything — NHL, NFL, college football, MLB, weather, flights, holidays — uses the same contracts and is registered
with entry points in `pyproject.toml`. Third-party packages do exactly the same. There are three you will
use (source, board, detector) and a fourth, optional one: a simulation, which lets the Simulator page drive
your boards by hand.

## Data source
```python
class MyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="My source")   # title = UI section name
    refresh_seconds: int = Field(300, ge=60, description="Shown as help text in the UI")

class MySource:
    key = "my"                      # snapshot namespace: my.<subkey>
    config_model = MyConfig
    async def run(self, ctx):       # loops forever; crashes are restarted with backoff
        while True:
            cfg = ctx.config        # re-read each loop: live edits apply
            data = await ctx.http.get(...)                     # ctx.http is a shared httpx.AsyncClient
            ctx.publish(data.json(), subkey="latest")          # -> "my.latest"
            await asyncio.sleep(cfg.refresh_seconds)
```
`ctx.timezone` (IANA) and `ctx.location` ((lat, lon) or None) come from the app config.
Sleep between polls with `await ctx.sleep(seconds)` (not `asyncio.sleep`): it records when you will fetch next.
Requests made through `ctx.http`, calls to `ctx.publish()` and crashes are counted per source automatically and
shown under *Data sources* on the dashboard and diagnostics pages (`GET /api/sources`): status
(starting / ok / degraded / offline after 3 consecutive failed requests / crashed), last OK, next poll, latency,
last error, published keys. If a source runs several loops, only call `ctx.sleep` from the main one.
Register: `[project.entry-points."scoreboard.sources"] my = "pkg.module:MySource"`.

## Board
```python
class MyBoard(BaseBoard):
    key = "my.card"; title = "My card"; config_model = MyBoardConfig
    requires = frozenset({"my.latest"})     # skipped by the director until present and non-empty
    def render(self, ctx, cfg) -> Image:    # pure; ctx.snapshot, ctx.elapsed, ctx.now, ctx.profile, ctx.width/height
        data = ctx.snapshot.get("my.latest")
        tree = VBox([Text("HELLO", load_font("pl", 6)), Sheen(Text(...), period=2)])
        return render_tree(tree, ctx.width, ctx.height, t=ctx.elapsed)
    def done(self, ctx, cfg) -> bool:       # optional: self-terminating boards (tickers/scrollers)
        return ctx.elapsed > 10
    def auto_seconds(self, ctx, cfg):       # override alongside done: the same length, as a number
        return 10.0                         # None = never ends itself; the web UI prints this next to "auto"
```
Use `enter(ctx, cfg)` to pre-render once when the board becomes active. `SequenceMixin` turns a board
into `build(ctx, cfg) -> Sequence` for timeline boards. Layout/animation vocabulary: `render/__init__.py`.
Register: `[project.entry-points."scoreboard.boards"] "my.card" = "pkg.module:MyBoard"`.

## Event board (interrupts the rotation)
```python
class MyAlert(SequenceMixin, EventBoard):
    key = "my.alert"; event_kinds = frozenset({"my.thing"})
    def matches(self, event, cfg): return cfg.enabled
    def build(self, ctx, cfg): ...            # ctx.event.payload
```
Emit events with a detector: `def detect(prev: Snapshot, new: Snapshot) -> Iterable[Event]` registered
under `scoreboard.detectors`. Diff the two snapshots; never keep state in the detector.
`EventBoard` sets `playlistable = False`: the director keeps such a board out of every rotation (it would hold a
blank frame with no event behind it), the web UI keeps it out of the playlist pickers and marks any saved entry
that still names one, and a new event on the board already showing re-enters it, so a cached `Sequence` is
rebuilt for the new payload.

## Sport packages
Publish a normalised game dict (docs/DATA.md) under `<sport>.main_event`, set `sport` on boards that only
apply to that sport, and reuse `nhl.select.select_main_event` / the NHL boards as base classes
(`nfl/` is the worked example: ~600 lines for a whole league; `ncaaf/` shows how little a sibling league on the
same API costs — it subclasses the NFL source, client and boards and only owns its team registry, conference
standings and the rank/slate touches; `mlb/` shows a sport whose live board needs
its own centre column — override `_live` / `_live_stats_row` / `_indicators` on the NHL `GameBoard` and keep the rest).

## Simulation (optional: drive your boards from the browser)
A simulation claims the snapshot keys your source publishes and writes values there on demand, so the
Simulator page can exercise your boards and interrupts with no feed behind them. It is not a mock: the
detectors, the arbiter and the director all run on what it publishes.
```python
class MyOptions(BaseModel):                       # the start form; the page draws it from the JSON schema
    model_config = ConfigDict(frozen=True, extra="forbid", title="My feed")
    level: Literal["watch", "warning"] = "warning"

class MySim:
    key = "my"; title = "My feed"; description = "Raise and clear a thing."
    options_model = MyOptions
    claims = frozenset({"my.latest"})              # keys taken over while running; must not overlap another sim's
    def start(self, options, ctx):  self.opts, self.items = options, []      # ctx: now (monotonic), wall, snapshot, config
    def tick(self, ctx):            return False   # advance to ctx.now; True when values() changed
    def actions(self):              return [Action("raise", "Raise", "Things", (Param("text", "Text"),), primary=True),
                                            Action("clear", "Clear", "Things", enabled=bool(self.items))]
    def action(self, name, params, ctx):
        if name == "raise": self.items.append({"level": self.opts.level, "text": params.get("text", "")})
        elif name == "clear": self.items = []
        else: raise SimError(f"unknown action {name!r}")
    def values(self):               return {"my.latest": list(self.items)}
    def describe(self):             return {"headline": f"{len(self.items)} active", "lines": [["Level", self.opts.level]]}
```
`actions()` is asked after every change, so return only what makes sense now (an engine mid-intermission
does not offer "goal"); `Param` kinds are `select` (with `(value, label)` options), `text`, `number`,
`bool`. `SimError` becomes a 422 with your message on the page. `nhl/sim.py` is the worked example.
Register: `[project.entry-points."scoreboard.sims"] my = "pkg.module:MySim"`.

## Testing
Record a real API response into `tests/fixtures/<plugin>/`, test the normaliser as a pure function,
drive the source with `respx`, and render boards at `(128,64)` and `(64,32)` asserting `img.getbbox()`.
