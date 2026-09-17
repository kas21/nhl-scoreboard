# User guide

## What it does
A small LED panel on your Pi that follows your team: a live scoreboard with goal and penalty alerts
during games, and a rotation of useful boards the rest of the time (scores around the league,
standings, your team's record and next game, clock, weather, holiday countdown, aircraft overhead).
NHL is the main event; NFL, college football (FBS) and MLB work the same way. Everything is set up from a web page — no files to edit.

## First run
1. Flash Raspberry Pi OS (64-bit), set Wi-Fi + hostname + user in Raspberry Pi Imager.
2. Install (see HARDWARE.md for wiring and the one-line installer).
3. Open `http://<hostname>.local:8080` from a phone or laptop on the same Wi-Fi.
4. The **setup wizard** opens automatically:
   - **Your panel** — pick the size and driver board. The panel shows a test pattern.
   - **Colours & orientation** — fix the colour order / rotation by looking at the panel; press
     *Apply* (restarts the display driver, ~5 s).
   - **Your team** — favourites in priority order; the first one is followed. Drag a pill to change the
     order (here and on the Settings page). The list offers the teams the
     scoreboard knows; a new or relocated team can be typed as its code before the app catches up (it shows
     with neutral colours until then, and the diagnostics page says so).
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
- **Dashboard** — live preview of exactly what the panel shows, state, brightness slider, a *Rotation* card and two info cards.
  *Rotation* draws the current playlist as one lap: a bar with a slice per board, sized by how long it runs, with what
  the board is made of above it (7 games, 4 aircraft, 2 pages) and its name and length below; the slice on screen fills
  as it plays and the header says what comes next. Auto lengths are marked ≈ because they follow the data. Boards the
  panel is passing over are listed underneath with the reason (no data, disabled, interrupt board, paused after an
  error, nothing to show), and a goal or other interrupt shows as a banner while it plays. The same card sits at the
  top of the *Boards* page.
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
  time. Enable/disable, set seconds. The seconds mean one of two things, and the row says which:
  - For a board that shows a list one item at a time — the score tickers, flights nearby, the holiday
    countdown, weather alerts — the box reads `s per game` (aircraft, holiday, alert) and is how long each
    item stays up. The board goes through everything it has and then moves on: 15 s per game with 3 games
    is 45 s. The row shows the total for what there is right now (`15 games ≈ 2:00`). Blank uses the
    board's own per-item setting from its section on the Settings page.
  - For every other board the number is how long the board shows, full stop. Leave it blank for "auto" —
    the board runs its own length. The row then shows what that works out to right now (`auto ≈ 24s`).
    A board with no length of its own says `auto · until the state changes` — it holds the screen until the
    state does (that is what the *live* game board wants), so give it seconds if you want the playlist to
    move on. Standings and team summary only know their length after they have run once (`auto · length not
    known yet` until then).
- **Settings** — every option, grouped: Display, Location, Brightness (fixed / sunrise-sunset / hours),
  Transition between boards, Sports (priority, and the game-day rollover hour: last night's finals stay in the
  ticker until then — today's games show as soon as the date turns, and the postgame board still leaves at
  midnight), per-board settings, per-data-source settings, Integrations (follow another panel, MQTT — see
  below).
- **Simulator** — run a game by hand to see what the panel does: pick two teams, drop the puck, start and stop
  the clock, score (with or without naming the scorer), call penalties, pull a goalie, end periods. The panel
  follows it exactly as it would a real game — the live board, the goal and penalty alerts, the ticker, the
  intermission and final playlists — while the real feed keeps polling underneath, out of sight. A *Simulating*
  badge shows on every page until you stop it (the ✕ on the badge stops everything); the real data is back
  on the panel the moment you do. Useful in the off-season, for checking a playlist, or for showing someone
  the goal animation without waiting for one. Options: which side counts as your team (auto follows your NHL
  favourites), preseason / regular / playoff rules, period length, clock speed, and whether to begin before
  the game or with the puck dropped. Only your team's goals get the full celebration; a goal by the other
  side gets the scorer card and the building's answer (WHO CARES?!), so pick a side
  (or a game your favourite is in) to see the whole animation. Nothing is saved; a restart ends it.
- **Diagnostics** — recent log lines and the data sources table. A source's *Drift* count says how often the
  feed did not look the way the scoreboard expects (a renamed field, a value it has never seen, a team not in
  its registry); open the row to read the notes. The panel keeps drawing its best guess meanwhile, so a
  growing count on a game night is worth a look even when nothing errors.

## Boards
| Board | Shows | Needs |
|---|---|---|
| NHL game / NFL game / College football game / MLB game | your team's game: pregame matchup, live score with period/clock, PP / empty net (NHL) or possession, down & distance, spot of the ball, a scrolling last play, red zone, timeouts (NFL and college, which also puts the poll rank in front of a ranked team's record) or inning + half, bases, count, outs, pitcher / batter, due up, last pitch (MLB), final (with hits and W/L/S pitchers for MLB) | a favourite with a game today |
| Goal / Touchdown / Home run | full-screen celebration + scorer card (NHL; the other team's goals get the card, then WHO CARES?!); runs that are not homers get a short card (MLB, off for the other team by default) | live game |
| Penalty | referee animation + details card | live game |
| Ticker | every game on today's slate, led by last night's finals until the game-day rollover hour (college: the games the source's `slate` setting keeps — ranked teams by default, or your conferences, or all sixty-odd; your favourites' games always) | slate within `show_games_within_days` |
| Standings | division / wildcard / league (GB column for MLB; college shows one conference per page with a CONF record column, your favourites' conferences only unless you turn `favorite_conferences_only` off, and `wildcard` means the divisions of conferences that still have them); "FINAL yyyy-yy" banner in the off-season | — |
| Team summary | record, streak, last result, next game (college: rank, conference record and place) | favourites |
| Season countdown | days until your team's opener / preseason (spring training) / kickoff / opening day | off-season & preseason |
| Weather alerts | the watches, warnings and advisories in force at your location (red / orange / yellow bar, the hazard, until when, where, and the agency's description paged underneath on 128x64); only appears while one is in force | location (US via the National Weather Service, Canada via Environment Canada) |
| Clock, Weather, Holiday countdown, Flights nearby / overhead | — | location for weather & flights |

## Alerts
Goals/penalties/touchdowns/runs come from the same data the score uses (polled every 5 s NHL / 20 s NFL and
college / 10 s MLB while your team plays), so nothing is missed if a poll fails. The other team's NHL goals get
the same scorer card (the PA announcement), then a WHO CARES?! chant in your team's colours (`opponent_duration` seconds); `opponent_goals` turns it off. `delay_seconds` (NHL and MLB sources) holds updates back to match a TV
broadcast. MLB inning breaks stay in the *live* state (the board shows MID/END and who is due up) rather
than switching to the intermission playlist seventeen times a game.

Weather alerts interrupt the rotation once per new alert — a flash of the level's colour, then the card
for `duration` seconds. Warnings only by default (`min_level` on the *Weather alert interrupt* board), and
they do interrupt a live game unless you turn `interrupt_live_game` off; watches and advisories still show
on the alerts board between periods either way. The *Weather alerts* source decides what exists at all:
`min_level` (advisories and up by default; statements are mostly noise) and an `ignore` word list that
drops marine alerts unless you live on the water. Note the NWS answers by county zone, so a warning for
the far end of your county shows too.

## Off-season behaviour
Standings from a finished season carry a FINAL banner; far-off game days don't show as "tonight";
the countdown board takes the front. This is all automatic from the league calendars.

## More than one panel
One scoreboard on the network can do all the fetching and feed the others. On the second panel, Settings →
Integrations → *Follower*: tick *enabled*, put the first panel's web address in *master url*
(`http://nhl-led-scoreboard-office.local:8080`, say) and restart it (Setup → restart, or power-cycle). From
then on it fetches nothing itself: every score, standing, forecast and plane comes from the master, the
moment the master has it. It still has its own display settings, brightness schedule, playlists and board
settings, so one panel can rotate through everything while another sits on the game. Team logos are the one
thing a follower still downloads itself. The Dashboard says which panel it is following, and the Diagnostics
sources table shows the link as a single `follower` row. If the master goes away the follower keeps its last
data, shows the stale dot after a few failed rounds, and picks up where it left off when the master is back.
Starting a simulation on the master runs it on every follower too.

## MQTT (Home Assistant and friends)
Settings → Integrations → *MQTT*: tick *enabled*, give it the broker's host (and port, user and password if
it wants them). The Dashboard status card says whether it is connected. Everything is published under
*topic prefix* (`scoreboard` by default; give each panel its own):

| Topic | What | Notes |
|---|---|---|
| `scoreboard/status` | `online` / `offline` | retained; `offline` is set by the broker if the panel drops |
| `scoreboard/state` | `{state, board, brightness, override}` | retained, on change |
| `scoreboard/snapshot/<key>` | the data, one topic per snapshot key with dots as slashes: `snapshot/main_event`, `snapshot/nhl/scores`, `snapshot/weather/current`… | retained, on change (see [DATA.md](DATA.md) for shapes). *snapshot keys* (advanced) narrows it to the keys you name |
| `scoreboard/event/<kind>` | `{kind, team, payload, ts}` — `event/nhl/goal`, `event/nhl/penalty`, `event/nfl/touchdown`, `event/flights/overhead`… | one message per event, not retained |
| `scoreboard/cmd/power` ← | `off` blanks the panel until `on` | an override; a restart clears it |
| `scoreboard/cmd/board` ← | a board key (`clock`), or `{"board": "nhl.standings", "seconds": 120}`; empty or `none` clears | forces a board, like the *preview* button |

A Home Assistant sensor for the score, for instance, is an `mqtt` sensor on `scoreboard/snapshot/main_event`
with a `value_template` of `{{ value_json.home.score }}`; a switch that turns the panel off overnight publishes
`off` / `on` to `scoreboard/cmd/power`.

## Updates
The Dashboard tells you when a new version is available and updates with one click (the panel goes dark for
~10 s while it restarts). Nothing else to do.

## If something looks wrong
- Colours swapped / mirrored → Setup → Colours & orientation → Apply.
- Flicker → Setup → Flicker fix (GPIO slowdown) → Apply; make sure the install ran `pi_tuning.sh`.
- Stale red dot bottom-right → the data feed is unreachable; last known data is shown until it returns.
- Reset everything → Settings → *Reset to defaults*.
