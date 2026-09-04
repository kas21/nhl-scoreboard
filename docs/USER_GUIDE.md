# User guide

## What it does
A small LED panel on your Pi that follows your team: a live scoreboard with goal and penalty alerts
during games, and a rotation of useful boards the rest of the time (scores around the league,
standings, your team's record and next game, clock, weather, holiday countdown, aircraft overhead).
NHL is the main event; NFL and MLB work the same way. Everything is set up from a web page — no files to edit.

## First run
1. Flash Raspberry Pi OS (64-bit), set Wi-Fi + hostname + user in Raspberry Pi Imager.
2. Install (see HARDWARE.md for wiring and the one-line installer).
3. Open `http://<hostname>.local:8080` from a phone or laptop on the same Wi-Fi.
4. The **setup wizard** opens automatically:
   - **Your panel** — pick the size and driver board. The panel shows a test pattern.
   - **Colours & orientation** — fix the colour order / rotation by looking at the panel; press
     *Apply* (restarts the display driver, ~5 s).
   - **Your team** — favourites in priority order; the first one is followed.
   - **Where you are** — search your town (sets timezone, and location for weather/flights/sunset dimming).
   - **Name** — the address you'll use (`name.local:8080`).
5. Finish. The wizard is always available again under **Setup**.

Team logos aren't bundled — the app downloads them once from ESPN's CDN the first time it runs
(~4 MB for both leagues, a second or two) and caches them on disk: `/var/cache/scoreboard/logos/`
on a Pi install, `~/.scoreboard/cache/logos/` when you run it yourself. Until that finishes, teams
show as a plain coloured tile. Delete a file there to re-fetch it, or drop your own PNG in its place
to override one.

### Alternate logos

A club's primary logo is not always the one that reads best on a panel. Some are wordmarks that
turn to mush at 22px (Washington, Los Angeles); others are dark marks that vanish against a black
panel (Tampa Bay, Toronto). ESPN publishes several variants per team, and **Settings -> LogosConfig**
picks between them:

- **Use curated defaults** (on): the audited picks for the six NHL teams whose default genuinely
  fails — Colorado, Los Angeles, Tampa Bay, Toronto, Vancouver and Washington. Every other team is
  untouched.
- **Overrides**: your own choice for any team, keyed `<sport>:<ABBREV>` (e.g. `nhl:CHI`), since NHL,
  NFL and MLB all have a `WSH`. An override always beats the curated pick.

Variants are `default`, `dark`, and the primary/secondary mark in several treatments —
`secondary_on_black` is usually the one you want for an alternate mark on a dark panel. The new
art downloads within a few seconds of saving; until it lands the team keeps its old logo, and
nothing needs a restart.

## Pages
- **Dashboard** — live preview of exactly what the panel shows, state, brightness slider, plus two info cards:
  *Games* lists every game for the next few days per sport (as far ahead as that sport's *show games within days*
  setting; MLB lists only today's games unless you turn off *schedule today only*, with your teams' records and next game,
  and the game the panel is following marked), and *Around you*
  shows the weather, the planes nearby and the next holidays when those extras are on. The planes list also keeps
  score: every airframe that comes into range is logged by tail number, and each row says how many visits it has
  made, with the regulars summed up above the list. The flight boards can show the same count
  (*show sightings*, off by default).
- **Boards** — per-state playlists. States: *offseason*, *offday* (season on, no game today), *pregame*,
  *live*, *intermission*, *postgame*. Reorder by dragging a row's grip (⠿) — the list reorders
  under the pointer and saves when you let go, Esc cancels; the arrows still move one place at a
  time. Enable/disable, set seconds. Leave the seconds blank for "auto" — the board runs its own
  length, e.g. a ticker goes through every game once. The row then shows what that works out to
  right now (`auto ≈ 24s`); it follows the data, so it moves as games come and go.
  A board with no length of its own says `auto · until the state changes` — it holds the screen until the
  state does (that is what the *live* game board wants), so give it seconds if you want the playlist to
  move on. Standings and team summary only know their length after they have run once (`auto · length not
  known yet` until then).
- **Settings** — every option, grouped: Display, Location, Brightness (fixed / sunrise-sunset / hours),
  Transition between boards, Sports priority, per-board settings, per-data-source settings.
- **Diagnostics** — recent log lines.

## Boards
| Board | Shows | Needs |
|---|---|---|
| NHL game / NFL game / MLB game | your team's game: pregame matchup, live score with period/clock, PP / empty net (NHL) or possession, down & distance, red zone, timeouts (NFL) or inning + half, bases, count, outs, pitcher / batter, due up, last pitch (MLB), final (with hits and W/L/S pitchers for MLB) | a favourite with a game today |
| Goal / Touchdown / Home run | full-screen celebration + scorer card (NHL); runs that are not homers get a short card (MLB, off for the other team by default) | live game |
| Penalty | referee animation + details card | live game |
| Ticker | every game on today's slate | slate within `show_games_within_days` |
| Standings | division / wildcard / league (GB column for MLB); "FINAL yyyy-yy" banner in the off-season | — |
| Team summary | record, streak, last result, next game | favourites |
| Season countdown | days until your team's opener / preseason (spring training) / kickoff / opening day | off-season & preseason |
| Clock, Weather, Holiday countdown, Flights nearby / overhead | — | location for weather & flights |

## Alerts
Goals/penalties/touchdowns/runs come from the same data the score uses (polled every 5 s NHL / 20 s NFL /
10 s MLB while your team plays), so nothing is missed if a poll fails. A short flash for the other team's
goals can be turned off per board. `delay_seconds` (NHL and MLB sources) holds updates back to match a TV
broadcast. MLB inning breaks stay in the *live* state (the board shows MID/END and who is due up) rather
than switching to the intermission playlist seventeen times a game.

## Off-season behaviour
Standings from a finished season carry a FINAL banner; far-off game days don't show as "tonight";
the countdown board takes the front. This is all automatic from the league calendars.

## Updates
The Dashboard tells you when a new version is available and updates with one click (the panel goes dark for
~10 s while it restarts). Nothing else to do.

## If something looks wrong
- Colours swapped / mirrored → Setup → Colours & orientation → Apply.
- Flicker → Setup → Flicker fix (GPIO slowdown) → Apply; make sure the install ran `pi_tuning.sh`.
- Stale red dot bottom-right → the data feed is unreachable; last known data is shown until it returns.
- Reset everything → Settings → *Reset to defaults*.
