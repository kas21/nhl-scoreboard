# MLB fixtures

Shaped like `statsapi.mlb.com` responses (the endpoints in `scoreboard/mlb/api.py`), built
with a generator rather than captured: the sandbox these were authored in could not reach
`statsapi.mlb.com`. Key names, nesting and value types follow the Stats API as MLB-LED-Scoreboard
reads it; the scores and records are fiction for 2026-09-03.

| File | Endpoint |
|---|---|
| `schedule_2026-09-03.json` | `schedule?sportId=1&startDate=2026-09-03&endDate=2026-09-03&hydrate=team,linescore,probablePitcher,decisions` — a final, a final in 11, a live top-7th at-bat (LAD @ SD), a mid-inning break (SEA @ HOU), a warm-up, a scheduled game with probables, a rain-out |
| `feed_live_776002.json` | `v1.1/game/776002/feed/live?fields=…` (LAD @ SD: Ohtani just homered) |
| `feed_live_776001.json` | same for the NYY @ BOS final, with pitching decisions |
| `standings_2026.json` | `standings?leagueId=103,104&season=2026&standingsTypes=regularSeason` |
| `schedule_NYY_2026-08-24_2026-09-17.json` | `schedule?sportId=1&teamId=147&startDate=…&endDate=…` (team summary window) |
| `schedule_NYY_opener.json` | the same for opening week (season countdown) |
| `seasons_2026.json` | `seasons?sportId=1&season=2026` |

To replace any of them with a real capture, `curl` the URL above into the file; the tests assert
on structure the API guarantees (ids, status blocks, linescore, records), not on these numbers.
