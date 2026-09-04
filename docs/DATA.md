# Data model

All snapshot values are plain JSON-shaped dicts/lists (immutable by convention: sources publish new objects).

## Snapshot keys
| Key | Producer | Shape |
|---|---|---|
| `main_event` | MainEventArbiter | the chosen game dict (below) with `sport`, `favorite_side`; or None |
| `nhl.main_event`, `nfl.main_event`, `mlb.main_event` | sport sources | that sport's candidate |
| `nhl.scores`, `nfl.scores`, `mlb.scores` | sources | list of game dicts for the slate (empty when beyond `show_games_within_days`) |
| `nhl.schedule`, `nfl.schedule`, `mlb.schedule` | sources | list of game dicts dated today .. today + `show_games_within_days` (the dashboard's games list; NHL walks `/schedule/{date}` weeks hourly, MLB and NFL come from the slate fetch) |
| `nhl.standings`, `nfl.standings`, `mlb.standings` | sources | `{teams:{ABBR:row}, division:{name:[ABBR]}, wildcard:{conf:{group:[ABBR]}}, league:[ABBR]}` (MLB rows add `games_back`, `wildcard_games_back`, `win_pct`, `eliminated`) |
| `nhl.team_summary`, `nfl.team_summary`, `mlb.team_summary` | sources | `{ABBR: {record:{wins,losses,otl,points,gp,l10,streak,division,division_rank,…}, prev_game, next_game}}` |
| `nhl.season`, `nfl.season`, `mlb.season` | sources | `{sport, phase: offseason|preseason|regular|playoffs, …dates, days_to_*, standings_final, first_game, favorite}` |
| `system` | NHL source | `{online: bool, failures: n}` |
| `holidays.upcoming` | holidays | `[{name, display, date, days, image, custom}]` — `display` is the alternate name if one is set, `image` an absolute path or null |
| `holidays.available` | holidays | `[{name, display, enabled, custom, image, image_name, image_slug, uploaded}]` — every holiday the calendar knows, on or off, for the Holidays page. `image_name` is the stem of the picture it shows now; `image_slug` is where an upload for that row would go, and they differ whenever a row borrows another's art |
| `flights.nearby`, `flights.overhead` | flights | `[aircraft]` sorted by distance |
| `weather.current`, `weather.daily` | weather | current conditions dict; `[day]` |

## Game dict (shared by NHL, NFL and MLB boards)
```
id, sport, type (1 pre / 2 regular / 3 playoff), state (raw), phase (pregame|live|intermission|postgame),
date (YYYY-MM-DD local), start_time_utc, week (NFL),
away/home: {abbrev, name, city, score, sog, record, color?, accent?, timeouts?, hits?, errors?, probable_pitcher?},
period (label: 1st/2nd/3rd/OT/SO | 1st..4th/HALF/OT | TOP/BOT/MID/END), period_number, clock (MLB: inning ordinal),
clock_running, in_intermission (never set by MLB: inning breaks stay live),
outcome ('' | FINAL | FINAL/OT | FINAL/SO | FINAL/2OT | FINAL/11 | PPD | CANCELLED | SUSPENDED),
powerplay {code: ev|a54|h53…, clock}, pulled_goalie (0|1 away|2 home|3 both), goals[], penalties[],   # NHL
situation {possession, down, distance, yard_line, red_zone, text, last_play}                          # NFL
situation {inning, inning_ordinal, half (top|bottom|middle|end), batting, balls, strikes, outs,          # MLB
           runners [1B,2B,3B], batter, on_deck, in_hole, pitcher, pitch_count, pitch {speed, code, label},
           last_play {type, label, text, complete, batting}, no_hitter, perfect_game, delay, note}
game_type (S/R/F/D/L/W), series ('SPRING' | 'WILD CARD' | 'NLDS GM2' | …), decisions {winner, loser, save}   # MLB
```

## Events (from diffing consecutive snapshots)
| Kind | Detector | Payload |
|---|---|---|
| `nhl.goal` / `nhl.goal_overturned` | `nhl/events.py` | side, count, goal {scorer, assists, goals_to_date, …}, score, game |
| `nhl.penalty` | " | penalty {team, type, desc, player, duration, period, time}, game |
| `nhl.state_change`, `nhl.powerplay` | " | old/new |
| `nfl.touchdown` / `nfl.field_goal` / `nfl.safety` | `nfl/events.py` | side, points, score, last_play, game |
| `mlb.home_run` / `mlb.run` | `mlb/events.py` | side, runs, score, inning, half, batter, text, game (a homer only when the live feed's current play says so) |
| `mlb.state_change`, `mlb.inning_change` | " | old/new; inning, half |
| `flights.overhead` | `extras/flights` | aircraft |
Event bursts collapse to the latest event per (kind, team).

## External APIs (all keyless)
| Source | Endpoints | Cadence |
|---|---|---|
| NHL `api-web.nhle.com/v1` | `score/now` (redirects to a dated URL — follow redirects), `gamecenter/{id}/landing` (situation, penalties, goals), `standings/now`, `club-schedule-season/{TEAM}/now`, `schedule/now` (season dates) | 5 s live / 60 s idle; standings+season hourly |
| ESPN `site.api.espn.com` | `…/football/nfl/scoreboard` (current week; `?dates=YYYYMMDD`), `apis/v2/…/nfl/standings`, `…/teams`, `…/teams/{id}/schedule` | 20 s live-day / 300 s; hourly |
| MLB Stats API `statsapi.mlb.com/api/v1` | `schedule?sportId=1&startDate&endDate&hydrate=team,linescore,probablePitcher,decisions` (slate + situation; `&teamId=` for a favourite's window), `v1.1/game/{pk}/feed/live?fields=…` (last play, last pitch, pitch count, no-hitter flags, decisions — only while a favourite is live), `standings?leagueId=103,104&season&standingsTypes=regularSeason`, `seasons?sportId=1&season` | 10 s live / 60 s idle; standings+season hourly |
| adsb.lol | `v2/lat/{lat}/lon/{lon}/dist/{nm}` | 30 s (airplanes.live now requires approval — not used) |
| adsbdb | `v0/callsign/{cs}` (route/airline, incl. ICAO/IATA operator codes) | cached 6 h / 1 h negative |
| Jxck-S/airline-logos (raw.githubusercontent.com) | `radarbox_logos/{CODE}.png`, then `flightaware_logos/{CODE}.png` | once per operator code; cached under `$SCOREBOARD_CACHE_DIR/airline-logos` (misses re-tried weekly) |
| FlightAware AeroAPI | `flights/{ident}` — optional, paid, daily budget | only when a key is set |
| ESPN CDN | `i/teamlogos/{nhl,nfl,mlb}/500/{code}.png` — team logos, none shipped in the repo (MLB codes are the Stats API's; `AZ`→`ari`, `CWS`→`chw`) | once per team on first run; cached under `$SCOREBOARD_CACHE_DIR/logos` |
| ESPN CDN | `guid/{team-guid}/logos/{variant}.png` — alternate marks (secondary, light treatments) | only for teams set to a variant; URL comes from `…/teams`, art downscaled to 500px on store |
| Open-Meteo | `v1/forecast` (+ geocoding for the wizard) | 10 min |
| `holidays` package | offline | hourly recompute |

`SCOREBOARD_CACHE_DIR` defaults to `~/.scoreboard/cache`; the systemd unit sets it to `/var/cache/scoreboard`.
`SCOREBOARD_DATA_DIR` (`~/.scoreboard/data`, `/var/lib/scoreboard` under systemd) holds what the *user*
supplied and nothing can re-download — currently `holidays/<slug>.png`, written only through
`POST /api/holidays/images/{slug}`, which re-encodes whatever you send to a PNG of at most 256px. Both live outside the checkout so
an OTA update, which fast-forwards the working tree, cannot delete them.

Fixtures under `tests/fixtures/` are real captures of each; tests never hit the network (respx).
