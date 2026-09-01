# Writing a board or data source

Everything — NHL, NFL, weather, flights, holidays — uses the same three contracts and is registered
with entry points in `pyproject.toml`. Third-party packages do exactly the same.

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

## Sport packages
Publish a normalised game dict (docs/DATA.md) under `<sport>.main_event`, set `sport` on boards that only
apply to that sport, and reuse `nhl.select.select_main_event` / the NHL boards as base classes
(`nfl/` is the worked example: ~600 lines for a whole league).

## Testing
Record a real API response into `tests/fixtures/<plugin>/`, test the normaliser as a pure function,
drive the source with `respx`, and render boards at `(128,64)` and `(64,32)` asserting `img.getbbox()`.
