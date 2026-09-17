# College hockey fixtures

Real captures of `site.api.espn.com` men's college hockey responses (the endpoints in
`scoreboard/ncaah/api.py`), taken 2026-09-17 and trimmed of what nothing reads: links,
broadcast and venue blocks, per-period line scores, the logo variants the cache does not use.
Key names, nesting and value types are as ESPN sent them.

| File | Endpoint |
|---|---|
| `espn_scoreboard_2026-01-10.json` | `…/hockey/mens-college-hockey/scoreboard?dates=20260110&limit=200` — a full Saturday slate, 27 finals (two in overtime) |
| `espn_scoreboard_2026-03-07.json` | the same for 2026-03-07: 21 finals, five in overtime |
| `espn_scoreboard_upcoming.json` | `…/scoreboard?limit=200` as answered in the off-season: the 2026-27 opening night, start times TBD (`timeValid: false`) |
| `espn_teams.json` | `…/teams?limit=1000` — every programme ESPN scores (116, D3 and Canadian schools included); the source keeps the D1 ones |
| `espn_schedule_MICH_2025-26.json` | `…/teams/130/schedule?season=2026` — Michigan's finished 2025-26 season, results and ranks (the team summary and record) |
| `espn_standings.json` | `apis/v2/sports/hockey/mens-college-hockey/standings` — the shell ESPN publishes for this league: conferences with one stale row. Kept so the test suite documents why nothing reads it |

There is no live game in the captures (the season had not started); tests wind a final back to
the second period in code, as `tests/test_nfl.py` does for football.
