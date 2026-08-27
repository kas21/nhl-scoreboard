# User guide

## What it does
A small LED panel on your Pi that follows your team: a live scoreboard with goal and penalty alerts
during games, and a rotation of useful boards the rest of the time (scores around the league,
standings, your team's record and next game, clock, weather, holiday countdown, aircraft overhead).
NHL is the main event; NFL works the same way. Everything is set up from a web page — no files to edit.

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

## Pages
- **Dashboard** — live preview of exactly what the panel shows, state, brightness slider.
- **Boards** — per-state playlists. States: *offseason*, *offday* (season on, no game today), *pregame*,
  *live*, *intermission*, *postgame*. Reorder, enable/disable, set seconds ("auto" lets the board decide,
  e.g. a ticker runs through every game once).
- **Settings** — every option, grouped: Display, Location, Brightness (fixed / sunrise-sunset / hours),
  Transition between boards, Sports priority, per-board settings, per-data-source settings.
- **Diagnostics** — recent log lines.

## Boards
| Board | Shows | Needs |
|---|---|---|
| NHL game / NFL game | your team's game: pregame matchup, live score with period/clock, PP / empty net (NHL) or possession, down & distance, red zone, timeouts (NFL), final | a favourite with a game today |
| Goal / Touchdown | full-screen celebration + scorer card (NHL) | live game |
| Penalty | referee animation + details card | live game |
| Ticker | every game on today's slate | slate within `show_games_within_days` |
| Standings | division / wildcard / league; "FINAL yyyy-yy" banner in the off-season | — |
| Team summary | record, streak, last result, next game | favourites |
| Season countdown | days until your team's opener / preseason / kickoff | off-season & preseason |
| Clock, Weather, Holiday countdown, Flights nearby / overhead | — | location for weather & flights |

## Alerts
Goals/penalties/touchdowns come from the same data the score uses (polled every 5 s NHL / 20 s NFL while
your team plays), so nothing is missed if a poll fails. A short flash for the other team's goals can be
turned off per board. `delay_seconds` (NHL source) holds updates back to match a TV broadcast.

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
