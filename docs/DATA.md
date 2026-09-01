# Data model

All snapshot values are plain JSON-shaped dicts/lists (immutable by convention: sources publish new objects).

## Snapshot keys
| Key | Producer | Shape |
|---|---|---|
| `main_event` | MainEventArbiter | the chosen game dict (below) with `sport`, `favorite_side`; or None |
| `nhl.main_event`, `nfl.main_event` | sport sources | that sport's candidate |
| `nhl.scores`, `nfl.scores` | sources | list of game dicts for the slate (empty when beyond `show_games_within_days`) |
| `nhl.standings`, `nfl.standings` | sources | `{teams:{ABBR:row}, division:{name:[ABBR]}, wildcard:{conf:{group:[ABBR]}}, league:[ABBR]}` |
| `nhl.team_summary`, `nfl.team_summary` | sources | `{ABBR: {record:{wins,losses,otl,points,gp,l10,streak,division,division_rank,…}, prev_game, next_game}}` |
| `nhl.season`, `nfl.season` | sources | `{sport, phase: offseason|preseason|regular|playoffs, …dates, days_to_*, standings_final, first_game, favorite}` |
| `system` | NHL source | `{online: bool, failures: n}` |
| `holidays.upcoming` | holidays | `[{name, display, date, days, image, custom}]` — `display` is the alternate name if one is set, `image` an absolute path or null |
| `holidays.available` | holidays | `[{name, display, enabled, custom, image}]` — every holiday the calendar knows, on or off, for the web UI picker |
| `flights.nearby`, `flights.overhead` | flights | `[aircraft]` sorted by distance |
| `weather.current`, `weather.daily` | weather | current conditions dict; `[day]` |

## Game dict (shared by NHL and NFL boards)
```
id, sport, type (1 pre / 2 regular / 3 playoff), state (raw), phase (pregame|live|intermission|postgame),
date (YYYY-MM-DD local), start_time_utc, week (NFL),
away/home: {abbrev, name, city, score, sog, record, color?, accent?, timeouts?},
period (label: 1st/2nd/3rd/OT/SO | 1st..4th/HALF/OT), period_number, clock, clock_running, in_intermission,
outcome ('' | FINAL | FINAL/OT | FINAL/SO | FINAL/2OT),
powerplay {code: ev|a54|h53…, clock}, pulled_goalie (0|1 away|2 home|3 both), goals[], penalties[],   # NHL
situation {possession, down, distance, yard_line, red_zone, text, last_play}                          # NFL
```

## Events (from diffing consecutive snapshots)
| Kind | Detector | Payload |
|---|---|---|
| `nhl.goal` / `nhl.goal_overturned` | `nhl/events.py` | side, count, goal {scorer, assists, goals_to_date, …}, score, game |
| `nhl.penalty` | " | penalty {team, type, desc, player, duration, period, time}, game |
| `nhl.state_change`, `nhl.powerplay` | " | old/new |
| `nfl.touchdown` / `nfl.field_goal` / `nfl.safety` | `nfl/events.py` | side, points, score, last_play, game |
| `flights.overhead` | `extras/flights` | aircraft |
Event bursts collapse to the latest event per (kind, team).

## External APIs (all keyless)
| Source | Endpoints | Cadence |
|---|---|---|
| NHL `api-web.nhle.com/v1` | `score/now` (redirects to a dated URL — follow redirects), `gamecenter/{id}/landing` (situation, penalties, goals), `standings/now`, `club-schedule-season/{TEAM}/now`, `schedule/now` (season dates) | 5 s live / 60 s idle; standings+season hourly |
| ESPN `site.api.espn.com` | `…/football/nfl/scoreboard` (current week; `?dates=YYYYMMDD`), `apis/v2/…/nfl/standings`, `…/teams`, `…/teams/{id}/schedule` | 20 s live-day / 300 s; hourly |
| adsb.lol | `v2/lat/{lat}/lon/{lon}/dist/{nm}` | 30 s (airplanes.live now requires approval — not used) |
| adsbdb | `v0/callsign/{cs}` (route/airline, incl. ICAO/IATA operator codes) | cached 6 h / 1 h negative |
| Jxck-S/airline-logos (raw.githubusercontent.com) | `radarbox_logos/{CODE}.png`, then `flightaware_logos/{CODE}.png` | once per operator code; cached under `$SCOREBOARD_CACHE_DIR/airline-logos` (misses re-tried weekly) |
| FlightAware AeroAPI | `flights/{ident}` — optional, paid, daily budget | only when a key is set |
| ESPN CDN | `i/teamlogos/{nhl,nfl}/500/{code}.png` — team logos, none shipped in the repo | once per team on first run; cached under `$SCOREBOARD_CACHE_DIR/logos` |
| ESPN CDN | `guid/{team-guid}/logos/{variant}.png` — alternate marks (secondary, light treatments) | only for teams set to a variant; URL comes from `…/teams`, art downscaled to 500px on store |
| Open-Meteo | `v1/forecast` (+ geocoding for the wizard) | 10 min |
| `holidays` package | offline | hourly recompute |

`SCOREBOARD_CACHE_DIR` defaults to `~/.scoreboard/cache`; the systemd unit sets it to `/var/cache/scoreboard`.
`SCOREBOARD_DATA_DIR` (`~/.scoreboard/data`, `/var/lib/scoreboard` under systemd) holds what the *user*
supplied and nothing can re-download — currently `holidays/<slug>.png`. Both live outside the checkout so
an OTA update, which fast-forwards the working tree, cannot delete them.

Fixtures under `tests/fixtures/` are real captures of each; tests never hit the network (respx).
