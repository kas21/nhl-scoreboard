# AHL fixtures

Real captures of the HockeyTech feed behind theahl.com (`lscluster.hockeytech.com`, the
endpoints in `scoreboard/ahl/api.py`), taken 2026-09-17. Key names, nesting and value types
are as the feed sent them; two files are cut down to a handful of rows.

| File | Endpoint |
|---|---|
| `scorebar.json` | `feed=modulekit&view=scorebar` — 13 rows picked from two captures (120 days back, 20 ahead): regulation, OT, shootout and double-OT finals from 2025-26 and the Calder Cup playoffs, then 2026 preseason (`EX`) and 2026-27 opening-night games |
| `gamesummary_1027771.json` | `feed=gc&tab=gamesummary&game_id=1027771` — Charlotte at Abbotsford, 2025-06-21 (Calder Cup final game 5, 4-3 OT): goals, penalties, shots, lineups |
| `clock_1027771.json` | `feed=gc&tab=clock` for the same game (not read by the app; kept for reference) |
| `seasons.json` | `feed=modulekit&view=seasons` — every season the feed knows, with dates and the playoff / preseason flags |
| `teamsbyseason.json` | `feed=modulekit&view=teamsbyseason&season_id=94` — the 32 clubs of 2026-27 with ids, divisions and logo URLs |
| `standings_2025-26.json` | `feed=statviewfeed&view=teams&groupTeamsBy=division&season=90…` — verbatim, parentheses and all: the feed wraps this one for JSONP |
| `schedule_ABB.json` | `feed=modulekit&view=schedule&team_id=440` — Abbotsford's first ten games of 2025-26 (played) followed by its first five of 2026-27 (scheduled), so a mid-season shape from two captures |

There is no live game in the captures; tests build one from a score-bar row and the game
summary (`LIVE_ROW` in `tests/test_ahl.py`).
