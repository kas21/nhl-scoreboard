# College football fixtures

Shaped like `site.api.espn.com` college football responses (the endpoints in
`scoreboard/ncaaf/api.py`), built with `generate.py` rather than captured: the sandbox
these were authored in could not reach ESPN. Key names, nesting and value types follow
ESPN's football feeds as the NFL captures in `../nfl` show them, plus the college-only
fields (`curatedRank`, `conferenceId`, lowercase `vsconf_*` standings stats, a conference
with nested divisions). The membership is the 2026 FBS alignment; the scores, records and
ranks are fiction for Saturday 2026-09-05 (week 2).

| File | Endpoint |
|---|---|
| `espn_scoreboard.json` | `…/football/college-football/scoreboard?groups=80&limit=200` — two finals (one in OT), a live 3rd quarter in the red zone (MICH @ OU), a halftime, and Saturday's remaining games |
| `espn_standings.json` | `apis/v2/sports/football/college-football/standings?group=80` — every FBS conference; the Sun Belt nested as East/West |
| `espn_teams.json` | `…/football/college-football/teams?groups=80&limit=200` — all 136 schools with colours and logo URLs |
| `espn_schedule_MICH.json` | `…/football/college-football/teams/130/schedule` (team summary) |

ESPN team ids are real for the well-known schools and synthetic (9000+) for the rest; nothing
reads them except the schedule URL. To replace any file with a real capture, `curl` the URL
above into it; the tests assert on structure ESPN guarantees, not on these numbers.
