# Data model

## Snapshot mechanics
Everything a board can see lives in one `Snapshot` (`data/store.py`): a frozen dataclass with `version`
(monotonic), `data` (key → value), `updated` (key → epoch seconds of the last publish) and `versions`
(key → the snapshot version that last published it, so `changed_since(v)` can hand a follower panel only
what is new). Sources call
`ctx.publish(value, subkey)` (→ `<source>.<subkey>`) or `ctx.publish_to(key, value)`; the store builds a new
snapshot with that one key replaced and hands `(prev, new)` to its listeners — the event bus, which runs the
detectors, and the arbiter, which recomputes `main_event`. Readers on the render thread take a reference
and never lock.

All values are plain JSON-shaped dicts and lists, published as new objects (never mutated in place).
`None` is a real value with a meaning of its own where a table below says so; `snapshot.get(key)` returns
`None` for a missing key as well, and `snapshot.has(key)` tells the two apart. A board's `requires` keys
must be present *and non-empty* for it to enter a playlist, so publishing `[]` or `None` is how a source
takes its board down. `snapshot.age(key)` gives seconds since the last publish; `GET /api/snapshot` dumps
the whole thing, and `GET /api/snapshot?since=<version>&wait=<seconds>` only the keys published after
that version, held open until there are some (or the wait runs out; a `since` beyond the current version,
i.e. the box restarted, gets everything). That is the whole protocol between a master panel and a follower
(`follower.py`): the follower republishes what it gets under the same keys, so nothing downstream knows.
The MQTT bridge (`mqtt.py`) mirrors every key to `<prefix>/snapshot/<key>` (dots as slashes), retained.

A key can be claimed by one owner (the simulator does this for `nhl.main_event` and `nhl.scores` while a
simulated game runs): other publishers' values are held back and the freshest one is republished the moment
the claim is released. A game dict published by the simulator carries `simulated: true`; nothing reads it,
but a board or a webhook that must not act on a fake goal can.

## Snapshot keys
| Key | Producer | Shape |
|---|---|---|
| `main_event` | MainEventArbiter | the chosen game dict (below) with `sport`, `favorite_side`; or None |
| `nhl.main_event`, `nfl.main_event`, `ncaaf.main_event`, `mlb.main_event`, `ncaah.main_event`, `ahl.main_event` | sport sources | that sport's candidate |
| `nhl.scores`, `nfl.scores`, `ncaaf.scores`, `mlb.scores`, `ncaah.scores`, `ahl.scores` | sources | list of game dicts for the slate (empty when beyond `show_games_within_days`; the college leagues trim it to the `slate` setting — ranked games, your conferences, or all — with favourites always in; NHL, MLB and AHL lead with last night's results until `sports.game_day_rollover_hour`, NFL and the college leagues carry the whole slate ESPN hands them) |
| `nhl.schedule`, `nfl.schedule`, `ncaaf.schedule`, `mlb.schedule`, `ncaah.schedule`, `ahl.schedule` | sources | list of game dicts dated today .. today + `show_games_within_days` (the dashboard's games list; NHL walks `/schedule/{date}` weeks hourly, MLB and NFL come from the slate fetch; MLB narrows it to today unless `schedule_today_only` is off) |
| `nhl.standings`, `nfl.standings`, `ncaaf.standings`, `mlb.standings`, `ahl.standings` | sources | `{teams:{ABBR:row}, division:{name:[ABBR]}, wildcard:{conf:{group:[ABBR]}}, league:[ABBR]}` (MLB rows add `games_back`, `wildcard_games_back`, `win_pct`, `eliminated`; college football's `division` is one list per conference, `wildcard` the divisions of conferences that still have them, and rows add `conference`, `conf_wins`, `conf_losses`, `conf_record`, `conference_rank`; the AHL's `wildcard` is each conference's two divisions and `otl` folds shootout losses in). There is no `ncaah.standings`: ESPN publishes none for college hockey |
| `nhl.team_summary`, `nfl.team_summary`, `ncaaf.team_summary`, `mlb.team_summary`, `ncaah.team_summary`, `ahl.team_summary` | sources | `{ABBR: {record:{wins,losses,otl,points,gp,l10,streak,division,division_rank,…}, prev_game, next_game}}` (college football adds `rank`, `conference`, `conf_record`, `conference_rank`; college hockey's record is W-L-T counted from the school's own schedule, with `ties` beside `otl` and `rank`) |
| `nhl.season`, `nfl.season`, `ncaaf.season`, `mlb.season`, `ncaah.season`, `ahl.season` | sources | `{sport, phase: offseason \| preseason \| regular \| playoffs, …dates, days_to_*, standings_final, first_game, favorite}` (the ESPN leagues take the phase and the next phase's start dates from the scoreboard's `leagues[0].calendar`, so the summer reads as off-season even though the slate already shows next season's opener) |
| `system` | NHL source (a follower: relayed from the master, or its own `{online: false, master}` when it cannot reach it) | `{online: bool, failures: n}` |
| `holidays.upcoming` | holidays | `[{name, display, date, days, image, custom}]` — `display` is the alternate name if one is set, `image` an absolute path or null |
| `holidays.available` | holidays | `[{name, display, enabled, custom, image, image_name, image_slug, uploaded}]` — every holiday the calendar knows, on or off, for the Holidays page. `image_name` is the stem of the picture it shows now; `image_slug` is where an upload for that row would go, and they differ whenever a row borrows another's art |
| `flights.nearby`, `flights.overhead` | flights | `[aircraft]` sorted by distance; with `count_sightings` on, each carries `sightings` (visits by this airframe, this one included) and `first_seen` (epoch seconds) |
| `flights.stats` | flights | `{airframes, sightings, today, since, regulars:[{hex, registration, type, operator, count, last_seen}]}` — the sighting log's totals. Keyed by ICAO hex; a visit is one appearance separated from the last by `visit_gap_minutes`. Persisted at `$SCOREBOARD_DATA_DIR/flights/sightings.json` |
| `weather.current`, `weather.daily` | weather | current conditions dict; `[day]` |
| `weather.alerts` | weather_alerts | `[{id, key, provider, event, name, level, severity, urgency, headline, summary, area, onset, expires, sender, references}]` in force at the location, most serious first, already filtered by the source's `min_level` / `ignore`; `level` is warning / watch / advisory / statement / other from the event name (`other` — Air Quality Alert, Civil Emergency Message — ranks with advisories). `key` is the hazard's identity, what the detector and the one-card-per-hazard dedupe key on: the NWS re-issues a warning under a new message id every update but each update `references` the messages it supersedes, so the key follows that chain back to the first message (a second, distinct warning of the same kind is a new chain and so is news); Environment Canada has no chain and uses its hazard code. Empty when nothing is in force (the source wakes at the soonest expiry to retire a lapsed alert between polls); `None` when unknown — nothing fetched yet, switched off, or just moved — which the detector treats as no baseline, so alerts already hours old are never reported as new |

## Game dict (shared by the NHL, NFL, MLB, college hockey and AHL boards)
```
id, sport, type (1 pre / 2 regular / 3 playoff), state (raw; NHL, football and college hockey: PPD/SUSP/CNCL when the schedule
state says the game is not being played — those are postgame, so a postponed favourite does not sit in pregame all night;
football's live states are LIVE and HALF), schedule_state (OK|PPD|SUSP|CNCL),
phase (pregame|live|intermission|postgame),
date (YYYY-MM-DD local), start_time_utc, week (NFL),
away/home: {abbrev, name, city, score, sog, record, color?, accent?, timeouts?, hits?, errors?, probable_pitcher?},
period (label: 1st/2nd/3rd/OT/SO | 1st..4th/HALF/OT | TOP/BOT/MID/END), period_number, clock (MLB: inning ordinal),
clock_running, in_intermission (never set by MLB: inning breaks stay live),
outcome ('' | FINAL | FINAL/OT | FINAL/SO | FINAL/2OT | FINAL/11 | PPD | CANCELLED | SUSPENDED),
powerplay {code: ev|a54|h53…, clock}, pulled_goalie (0|1 away|2 home|3 both), goals[], penalties[],   # NHL
situation {possession, down, distance, yard_line, red_zone, text, spot, last_play, last_play_type}    # NFL (spot: "KC 44"; last_play_type: ESPN's play type, e.g. "Safety")
situation {inning, inning_ordinal, half (top|bottom|middle|end), batting, balls, strikes, outs,          # MLB
           runners [1B,2B,3B], batter, on_deck, in_hole, pitcher, pitch_count, pitch {speed, code, label},
           last_play {type, label, text, complete, batting}, no_hitter, perfect_game, delay, note}
game_type (S/R/F/D/L/W), series ('SPRING' | 'WILD CARD' | 'NLDS GM2' | …), decisions {winner, loser, save}   # MLB
```

## Events (from diffing consecutive snapshots)
| Kind | Detector | Payload |
|---|---|---|
| `nhl.goal` / `nhl.goal_overturned` | `nhl/events.py` | side, count, goal {scorer, assists, goals_to_date, …}, score, game |
| `nhl.penalty` | " | penalty {team, type, desc, player, duration, period, time}, game (a penalty is new by identity, not by list position; while the landing feed is unreachable the last penalties and power play are carried, not dropped) |
| `nhl.state_change`, `nhl.powerplay` | " | old/new |
| `nfl.touchdown` / `nfl.field_goal` / `nfl.safety` / `nfl.score` (a 4- or 5-point swing) | `nfl/events.py` | side, points, score, last_play, game (classified from `last_play_type` when ESPN names the play, else from the points; a bare +2 is taken as a two-point try, not a safety) |
| `nfl.state_change` | " | old/new, game |
| `ncaaf.touchdown` / `ncaaf.field_goal` / `ncaaf.safety` / `ncaaf.score`, `ncaaf.state_change` | `ncaaf/events.py` | the same rule on `ncaaf.main_event` |
| `mlb.home_run` / `mlb.run` | `mlb/events.py` | side, runs, score, inning, half, batter, text, game (a homer only when the live feed's current play says so) |
| `mlb.state_change`, `mlb.inning_change` | " | old/new; inning, half |
| `ncaah.goal`, `ncaah.state_change` | `ncaah/events.py` | the NHL rule on `ncaah.main_event` (no scorer: ESPN has no goal feed, so `goal` is null; no penalties) |
| `ahl.goal` / `ahl.penalty` / `ahl.state_change` / `ahl.powerplay` | `ahl/events.py` | the NHL rule on `ahl.main_event`; goals carry `sweater` (jersey number) as well |
| `flights.overhead` | `extras/flights` | aircraft |
| `weather.alert` | `extras/weather/alerts` | alert, live_game (a game is live or in intermission); one per alert new to `weather.alerts`, least serious emitted first so the collapse keeps the top one |
Event bursts collapse to the latest event per (kind, team).

## External APIs (all keyless)
| Source | Endpoints | Cadence |
|---|---|---|
| NHL `api-web.nhle.com/v1` | `score/now` (redirects to a dated URL — follow redirects), `gamecenter/{id}/landing` (situation, penalties, goals), `standings/now`, `club-schedule-season/{TEAM}/now`, `schedule/now` (season dates), `schedule/{date}` (the weekly walk behind `nhl.schedule`) | 5 s live / 60 s idle; standings+season hourly |
| ESPN `site.api.espn.com` | `…/football/nfl/scoreboard` (current week; `?dates=YYYYMMDD`), `apis/v2/…/nfl/standings`, `…/teams`, `…/teams/{id}/schedule` | 20 s live-day / 300 s; hourly |
| ESPN `site.api.espn.com` (college) | the same under `…/football/college-football/`, with `?groups=80&limit=200` on the scoreboard (FBS only, whole slate), `?group=80` on standings and `?limit=1000` on teams (that endpoint ignores `groups` and lists ~760 programmes, spelling AFA/BUFF/JVST as AF/BUF/JXST — `logos.API_ABBREVS`); game sides carry `rank` from `curatedRank`; teams are labelled by school (`shortDisplayName`) | same cadence |
| ESPN `site.api.espn.com` (college hockey) | the same under `…/hockey/mens-college-hockey/`: `scoreboard?limit=200` (the whole D1 slate), `teams?limit=1000` (ids and logo URLs; `AFA`/`WISC` are spelled `AF`/`WIS` there — `logos.API_ABBREVS`), `teams/{id}/schedule` (a favourite's results: its record, team summary and rank come from here). The `standings` endpoint answers a shell with no rows and is not read | 15 s live / 300 s; schedules hourly |
| HockeyTech `lscluster.hockeytech.com/feed/index.php` (AHL) | `feed=modulekit&view=scorebar&numberofdaysback=1&numberofdaysahead=N` (the slate with live period, clock, score and records), `view=seasons`, `view=teamsbyseason` (ids, divisions, logo URLs), `view=schedule&season_id&team_id`, `feed=gc&tab=gamesummary&game_id` (goals, penalties, shots — only for the followed game, live or final; the power play is reconstructed from the penalty log against the clock), `feed=statviewfeed&view=teams&groupTeamsBy=division&season=…` (standings; the JSON arrives wrapped in parentheses). Key `ccb91f29d6744675` is the public one theahl.com embeds | 10 s live / 120 s idle; standings+seasons hourly |
| MLB Stats API `statsapi.mlb.com/api/v1` | `schedule?sportId=1&startDate&endDate&hydrate=team,linescore,probablePitcher,decisions` (slate + situation; `&teamId=` for a favourite's window), `v1.1/game/{pk}/feed/live?fields=…` (last play, last pitch, pitch count, no-hitter flags, decisions — only while a favourite is live), `standings?leagueId=103,104&season&standingsTypes=regularSeason`, `seasons?sportId=1&season` | 10 s live / 60 s idle; standings+season hourly |
| adsb.lol | `v2/lat/{lat}/lon/{lon}/dist/{nm}` | 30 s (airplanes.live now requires approval — not used) |
| adsbdb | `v0/callsign/{cs}` (route/airline, incl. ICAO/IATA operator codes) | cached 6 h / 1 h negative |
| Jxck-S/airline-logos (raw.githubusercontent.com) | `radarbox_logos/{CODE}.png`, then `flightaware_logos/{CODE}.png` | once per operator code; cached under `$SCOREBOARD_CACHE_DIR/airline-logos` (misses re-tried weekly) |
| FlightAware AeroAPI | `flights/{ident}` — optional, paid, daily budget | only when a key is set |
| ESPN CDN | `i/teamlogos/{nhl,nfl,mlb}/500/{code}.png` — team logos, none shipped in the repo (MLB codes are the Stats API's; `AZ`→`ari`, `CWS`→`chw`). College art (football and hockey) lives at `i/teamlogos/ncaa/500/{espn team id}.png`, so its URLs are read off the `…/teams` index rather than built from the abbreviation | once per team on first run; cached under `$SCOREBOARD_CACHE_DIR/logos` |
| HockeyTech assets `assets.leaguestat.com/ahl/logos/{team id}.png` | AHL club logos; the URL per club comes from `teamsbyseason` and is registered with `logos.register_urls` | once per club; same cache |
| ESPN CDN | `guid/{team-guid}/logos/{variant}.png` — alternate marks (secondary, light treatments) | only for teams set to a variant; URL comes from `…/teams`, art downscaled to 500px on store |
| Open-Meteo | `v1/forecast` (+ geocoding for the wizard) | 10 min |
| NWS `api.weather.gov` | `alerts/active?point=lat,lon` (CAP alerts whose zones cover the point; four decimals max; a point outside the US is a 400 "out of bounds", which `provider: auto` takes as the cue to switch) | 5 min |
| Environment Canada GeoMet `api.weather.gc.ca` | `collections/weather-alerts/items?bbox=…&lang=en` (one feature per forecast-region polygon; `status_en: ended` rows are still listed and are dropped) | 5 min |
| `holidays` package | offline | hourly recompute |

`SCOREBOARD_CACHE_DIR` defaults to `~/.scoreboard/cache`; the systemd unit sets it to `/var/cache/scoreboard`.
`SCOREBOARD_DATA_DIR` (`~/.scoreboard/data`, `/var/lib/scoreboard` under systemd) holds what the *user*
supplied and nothing can re-download — `flights/sightings.json` (the airframe visit log) and `holidays/<slug>.png`, the latter written only through
`POST /api/holidays/images/{slug}`, which re-encodes whatever you send to a PNG of at most 256px. Both live outside the checkout so
an OTA update, which fast-forwards the working tree, cannot delete them.

Fixtures under `tests/fixtures/` are real captures of each, except MLB and college football, which are generated
(see their READMEs); tests never hit the network (respx).
