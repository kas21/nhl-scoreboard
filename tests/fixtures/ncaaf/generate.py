"""Generate ESPN-shaped college football fixtures (see README.md). Run from the repo root:

    uv run python tests/fixtures/ncaaf/generate.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

HERE = Path(__file__).parent
SEASON = 2026
WEEK = 2
TODAY = "2026-09-05"          # a Saturday in week 2

# abbrev -> (school, nickname, colour, alternate). ESPN ids for the schools we are sure of; the rest are synthetic.
TEAMS: dict[str, tuple[str, str, str, str]] = {
    # ACC
    "BC": ("Boston College", "Eagles", "98002e", "bc9b6a"), "CAL": ("California", "Golden Bears", "003262", "fdb515"),
    "CLEM": ("Clemson", "Tigers", "f66733", "522d80"), "DUKE": ("Duke", "Blue Devils", "003087", "ffffff"),
    "FSU": ("Florida State", "Seminoles", "782f40", "ceb888"), "GT": ("Georgia Tech", "Yellow Jackets", "b3a369", "003057"),
    "LOU": ("Louisville", "Cardinals", "ad0000", "000000"), "MIA": ("Miami", "Hurricanes", "f47321", "005030"),
    "NCST": ("NC State", "Wolfpack", "cc0000", "000000"), "PITT": ("Pittsburgh", "Panthers", "003594", "ffb81c"),
    "SMU": ("SMU", "Mustangs", "0033a0", "c8102e"), "STAN": ("Stanford", "Cardinal", "8c1515", "ffffff"),
    "SYR": ("Syracuse", "Orange", "f76900", "000e54"), "UNC": ("North Carolina", "Tar Heels", "7bafd4", "ffffff"),
    "UVA": ("Virginia", "Cavaliers", "232d4b", "f84c1e"), "VT": ("Virginia Tech", "Hokies", "630031", "cf4420"),
    "WAKE": ("Wake Forest", "Demon Deacons", "9e7e38", "000000"),
    # Big 12
    "ARIZ": ("Arizona", "Wildcats", "cc0033", "003366"), "ASU": ("Arizona State", "Sun Devils", "8c1d40", "ffc627"),
    "BAY": ("Baylor", "Bears", "154734", "ffb81c"), "BYU": ("BYU", "Cougars", "002e5d", "ffffff"),
    "CIN": ("Cincinnati", "Bearcats", "e00122", "000000"), "COLO": ("Colorado", "Buffaloes", "cfb87c", "000000"),
    "HOU": ("Houston", "Cougars", "c8102e", "ffffff"), "ISU": ("Iowa State", "Cyclones", "c8102e", "f1be48"),
    "KSU": ("Kansas State", "Wildcats", "512888", "d1d1d1"), "KU": ("Kansas", "Jayhawks", "0051ba", "e8000d"),
    "OKST": ("Oklahoma State", "Cowboys", "ff7300", "000000"), "TCU": ("TCU", "Horned Frogs", "4d1979", "a3a9ac"),
    "TTU": ("Texas Tech", "Red Raiders", "cc0000", "000000"), "UCF": ("UCF", "Knights", "000000", "ffc904"),
    "UTAH": ("Utah", "Utes", "cc0000", "ffffff"), "WVU": ("West Virginia", "Mountaineers", "002855", "eaaa00"),
    # Big Ten
    "ILL": ("Illinois", "Fighting Illini", "e84a27", "13294b"), "IND": ("Indiana", "Hoosiers", "990000", "eeedeb"),
    "IOWA": ("Iowa", "Hawkeyes", "000000", "ffcd00"), "MD": ("Maryland", "Terrapins", "e03a3e", "ffd520"),
    "MICH": ("Michigan", "Wolverines", "00274c", "ffcb05"), "MINN": ("Minnesota", "Golden Gophers", "7a0019", "ffcc33"),
    "MSU": ("Michigan State", "Spartans", "18453b", "ffffff"), "NEB": ("Nebraska", "Cornhuskers", "e41c38", "ffffff"),
    "NW": ("Northwestern", "Wildcats", "4e2a84", "ffffff"), "ORE": ("Oregon", "Ducks", "154733", "fee123"),
    "OSU": ("Ohio State", "Buckeyes", "bb0000", "666666"), "PSU": ("Penn State", "Nittany Lions", "041e42", "ffffff"),
    "PUR": ("Purdue", "Boilermakers", "cfb991", "000000"), "RUTG": ("Rutgers", "Scarlet Knights", "cc0033", "ffffff"),
    "UCLA": ("UCLA", "Bruins", "2d68c4", "f2a900"), "USC": ("USC", "Trojans", "990000", "ffc72c"),
    "WASH": ("Washington", "Huskies", "4b2e83", "b7a57a"), "WIS": ("Wisconsin", "Badgers", "c5050c", "ffffff"),
    # SEC
    "ALA": ("Alabama", "Crimson Tide", "9e1b32", "ffffff"), "ARK": ("Arkansas", "Razorbacks", "9d2235", "ffffff"),
    "AUB": ("Auburn", "Tigers", "0c2340", "e87722"), "FLA": ("Florida", "Gators", "0021a5", "fa4616"),
    "LSU": ("LSU", "Tigers", "461d7c", "fdd023"), "MISS": ("Ole Miss", "Rebels", "ce1126", "14213d"),
    "MIZ": ("Missouri", "Tigers", "000000", "f1b82d"), "MSST": ("Mississippi State", "Bulldogs", "660000", "ffffff"),
    "OU": ("Oklahoma", "Sooners", "841617", "fdf9d8"), "SC": ("South Carolina", "Gamecocks", "73000a", "000000"),
    "TA&M": ("Texas A&M", "Aggies", "500000", "ffffff"), "TENN": ("Tennessee", "Volunteers", "ff8200", "ffffff"),
    "TEX": ("Texas", "Longhorns", "bf5700", "ffffff"), "UGA": ("Georgia", "Bulldogs", "ba0c2f", "000000"),
    "UK": ("Kentucky", "Wildcats", "0033a0", "ffffff"), "VAN": ("Vanderbilt", "Commodores", "866d4b", "000000"),
    # American
    "ARMY": ("Army", "Black Knights", "000000", "d4bf91"), "CLT": ("Charlotte", "49ers", "046a38", "b9975b"),
    "ECU": ("East Carolina", "Pirates", "592a8a", "fdc82f"), "FAU": ("Florida Atlantic", "Owls", "003366", "cc0000"),
    "MEM": ("Memphis", "Tigers", "003087", "898d8d"), "NAVY": ("Navy", "Midshipmen", "00205b", "c5b783"),
    "RICE": ("Rice", "Owls", "00205b", "c1c6c8"), "TEM": ("Temple", "Owls", "9d2235", "ffffff"),
    "TLSA": ("Tulsa", "Golden Hurricane", "002d72", "c5b358"), "TULN": ("Tulane", "Green Wave", "006747", "418fde"),
    "UAB": ("UAB", "Blazers", "1e6b52", "f4c300"), "UNT": ("North Texas", "Mean Green", "00853e", "000000"),
    "USF": ("South Florida", "Bulls", "006747", "cfc493"), "UTSA": ("UTSA", "Roadrunners", "0c2340", "f15a22"),
    # C-USA
    "DEL": ("Delaware", "Blue Hens", "00539f", "ffd200"), "FIU": ("Florida International", "Panthers", "081e3f", "b6862c"),
    "JVST": ("Jacksonville State", "Gamecocks", "cc0000", "000000"), "KENN": ("Kennesaw State", "Owls", "000000", "fdbb30"),
    "LIB": ("Liberty", "Flames", "0a254e", "a61c31"), "MOST": ("Missouri State", "Bears", "5e0009", "ffffff"),
    "MTSU": ("Middle Tennessee", "Blue Raiders", "0066cc", "ffffff"), "NMSU": ("New Mexico State", "Aggies", "861f41", "ffffff"),
    "SHSU": ("Sam Houston", "Bearkats", "f26622", "1a3668"), "WKU": ("Western Kentucky", "Hilltoppers", "c60000", "ffffff"),
    # MAC
    "AKR": ("Akron", "Zips", "041e42", "a89968"), "BALL": ("Ball State", "Cardinals", "ba0c2f", "ffffff"),
    "BGSU": ("Bowling Green", "Falcons", "fe5000", "4f2c1d"), "BUFF": ("Buffalo", "Bulls", "005bbb", "ffffff"),
    "CMU": ("Central Michigan", "Chippewas", "6a0032", "ffc82e"), "EMU": ("Eastern Michigan", "Eagles", "006633", "ffffff"),
    "KENT": ("Kent State", "Golden Flashes", "002664", "eaab00"), "M-OH": ("Miami (OH)", "RedHawks", "b61e2e", "000000"),
    "MASS": ("UMass", "Minutemen", "881c1c", "000000"), "OHIO": ("Ohio", "Bobcats", "00694e", "ffffff"),
    "TOL": ("Toledo", "Rockets", "15397f", "ffd100"), "WMU": ("Western Michigan", "Broncos", "6c4023", "b5a167"),
    # Mountain West
    "AFA": ("Air Force", "Falcons", "003087", "8a8d8f"), "HAW": ("Hawai'i", "Rainbow Warriors", "024731", "c8c8c8"),
    "NEV": ("Nevada", "Wolf Pack", "003366", "807f84"), "NIU": ("Northern Illinois", "Huskies", "c8102e", "000000"),
    "SJSU": ("San José State", "Spartans", "0055a2", "e5a823"), "UNLV": ("UNLV", "Rebels", "cf0a2c", "000000"),
    "UNM": ("New Mexico", "Lobos", "ba0c2f", "a7a8aa"), "UTEP": ("UTEP", "Miners", "041e42", "ff8200"),
    "WYO": ("Wyoming", "Cowboys", "492f24", "ffc425"),
    # Pac-12
    "BSU": ("Boise State", "Broncos", "0033a0", "d64309"), "CSU": ("Colorado State", "Rams", "1e4d2b", "c8c372"),
    "FRES": ("Fresno State", "Bulldogs", "db0032", "002e6d"), "ORST": ("Oregon State", "Beavers", "dc4405", "000000"),
    "SDSU": ("San Diego State", "Aztecs", "a6192e", "000000"), "TXST": ("Texas State", "Bobcats", "501214", "b5a36a"),
    "USU": ("Utah State", "Aggies", "0f2439", "ffffff"), "WSU": ("Washington State", "Cougars", "981e32", "5e6a71"),
    # Sun Belt
    "APP": ("Appalachian State", "Mountaineers", "222222", "ffcc00"), "ARST": ("Arkansas State", "Red Wolves", "cc092f", "000000"),
    "CCU": ("Coastal Carolina", "Chanticleers", "006f71", "a27752"), "GASO": ("Georgia Southern", "Eagles", "011e41", "87714d"),
    "GAST": ("Georgia State", "Panthers", "0039a6", "c60c30"), "JMU": ("James Madison", "Dukes", "450084", "cbb677"),
    "LT": ("Louisiana Tech", "Bulldogs", "002f8b", "e31b23"), "MRSH": ("Marshall", "Thundering Herd", "00b140", "000000"),
    "ODU": ("Old Dominion", "Monarchs", "003057", "7c878e"), "TROY": ("Troy", "Trojans", "8a2432", "b0b7bc"),
    "ULL": ("Louisiana", "Ragin' Cajuns", "ce181e", "0a0203"), "ULM": ("UL Monroe", "Warhawks", "800029", "cfb87c"),
    "USA": ("South Alabama", "Jaguars", "00205b", "bf0d3e"), "USM": ("Southern Miss", "Golden Eagles", "ffab00", "000000"),
    # Independents
    "CONN": ("UConn", "Huskies", "000e2f", "ffffff"), "ND": ("Notre Dame", "Fighting Irish", "0c2340", "c99700"),
}
CONFERENCES = {
    "ACC": ("Atlantic Coast Conference", 1, ["BC", "CAL", "CLEM", "DUKE", "FSU", "GT", "LOU", "MIA", "NCST", "PITT", "SMU", "STAN", "SYR", "UNC", "UVA", "VT", "WAKE"]),
    "Big 12": ("Big 12 Conference", 4, ["ARIZ", "ASU", "BAY", "BYU", "CIN", "COLO", "HOU", "ISU", "KSU", "KU", "OKST", "TCU", "TTU", "UCF", "UTAH", "WVU"]),
    "Big Ten": ("Big Ten Conference", 5, ["ILL", "IND", "IOWA", "MD", "MICH", "MINN", "MSU", "NEB", "NW", "ORE", "OSU", "PSU", "PUR", "RUTG", "UCLA", "USC", "WASH", "WIS"]),
    "SEC": ("Southeastern Conference", 8, ["ALA", "ARK", "AUB", "FLA", "LSU", "MISS", "MIZ", "MSST", "OU", "SC", "TA&M", "TENN", "TEX", "UGA", "UK", "VAN"]),
    "American": ("American Athletic Conference", 151, ["ARMY", "CLT", "ECU", "FAU", "MEM", "NAVY", "RICE", "TEM", "TLSA", "TULN", "UAB", "UNT", "USF", "UTSA"]),
    "CUSA": ("Conference USA", 12, ["DEL", "FIU", "JVST", "KENN", "LIB", "MOST", "MTSU", "NMSU", "SHSU", "WKU"]),
    "MAC": ("Mid-American Conference", 15, ["AKR", "BALL", "BGSU", "BUFF", "CMU", "EMU", "KENT", "M-OH", "MASS", "OHIO", "TOL", "WMU"]),
    "MWC": ("Mountain West Conference", 17, ["AFA", "HAW", "NEV", "NIU", "SJSU", "UNLV", "UNM", "UTEP", "WYO"]),
    "Pac-12": ("Pac-12 Conference", 9, ["BSU", "CSU", "FRES", "ORST", "SDSU", "TXST", "USU", "WSU"]),
    "Sun Belt": ("Sun Belt Conference", 37, ["APP", "ARST", "CCU", "GASO", "GAST", "JMU", "LT", "MRSH", "ODU", "TROY", "ULL", "ULM", "USA", "USM"]),
    "Ind": ("FBS Independents", 18, ["CONN", "ND"]),
}
# The Sun Belt still plays divisions: exercises the nested standings walk.
DIVISIONS = {"Sun Belt": {"Sun Belt - East": ["APP", "CCU", "GASO", "GAST", "JMU", "MRSH", "ODU"],
                          "Sun Belt - West": ["ARST", "LT", "TROY", "ULL", "ULM", "USA", "USM"]}}
KNOWN_IDS = {"MICH": 130, "OSU": 194, "UGA": 61, "ALA": 333, "TEX": 251, "ND": 87, "ORE": 2483, "PSU": 213, "LSU": 99,
             "CLEM": 228, "USC": 30, "OU": 201, "TENN": 2633, "MIA": 2390, "IOWA": 2294, "NEB": 158, "WIS": 275, "FSU": 52,
             "AUB": 2, "FLA": 57, "TA&M": 245, "UTAH": 254, "WASH": 264, "MISS": 145, "MIZ": 142, "COLO": 38, "ASU": 9,
             "BSU": 68, "BYU": 252, "IND": 84, "SMU": 2567, "ARMY": 349, "NAVY": 2426, "UCLA": 26, "MSU": 127, "ILL": 356}
RANKS = ["TEX", "OSU", "UGA", "ORE", "PSU", "ND", "ALA", "MICH", "CLEM", "LSU", "MIA", "TENN", "SMU", "ISU", "BSU", "ARIZ",
         "IND", "ILL", "KSU", "OU", "USC", "TA&M", "MISS", "IOWA", "UTAH"]
RANK_OF = {a: i + 1 for i, a in enumerate(RANKS)}
CONF_OF = {t: conf for conf, (_, _, ts) in CONFERENCES.items() for t in ts}
CONF_ID = {conf: cid for conf, (_, cid, _) in CONFERENCES.items()}


def team_id(abbrev: str) -> int:
    return KNOWN_IDS.get(abbrev, 9000 + sorted(TEAMS).index(abbrev))


def team(abbrev: str, full: bool = True) -> dict:
    school, nick, color, alt = TEAMS[abbrev]
    tid = team_id(abbrev)
    t = {"id": str(tid), "uid": f"s:20~l:23~t:{tid}", "location": school, "name": nick, "abbreviation": abbrev,
         "displayName": f"{school} {nick}", "shortDisplayName": school, "color": color, "alternateColor": alt,
         "isActive": True, "logo": f"https://a.espncdn.com/i/teamlogos/ncaa/500/{tid}.png",
         "conferenceId": str(CONF_ID[CONF_OF[abbrev]])}
    if full:
        t["logos"] = [{"href": f"https://a.espncdn.com/i/teamlogos/ncaa/500/{tid}.png", "width": 500, "height": 500, "alt": "", "rel": ["full", "default"]},
                      {"href": f"https://a.espncdn.com/i/teamlogos/ncaa/500-dark/{tid}.png", "width": 500, "height": 500, "alt": "", "rel": ["full", "dark"]}]
    return t


def status(state: str, period: int = 0, clock: str = "0:00", name: str | None = None) -> dict:
    names = {"pre": ("STATUS_SCHEDULED", "Scheduled", False), "in": ("STATUS_IN_PROGRESS", "In Progress", False),
             "post": ("STATUS_FINAL", "Final", True)}
    n, desc, done = names[state]
    if name:
        n, desc = name, "Halftime"
    ids = {"pre": "1", "in": "2", "post": "3"}
    return {"clock": 0.0, "displayClock": clock, "period": period,
            "type": {"id": ids[state], "name": n, "state": state, "completed": done, "description": desc, "detail": desc, "shortDetail": desc}}


def competitor(abbrev: str, home: bool, score: int | None, record: str, winner: bool = False, order: int = 0, schedule: bool = False) -> dict:
    c = {"id": str(team_id(abbrev)), "uid": f"s:20~l:23~t:{team_id(abbrev)}", "type": "team", "order": order,
         "homeAway": "home" if home else "away", "winner": winner, "team": team(abbrev, full=schedule),
         "curatedRank": {"current": RANK_OF.get(abbrev, 99)},
         "records": [{"name": "overall", "abbreviation": "Game", "type": "total", "summary": record},
                     {"name": "Home" if home else "Road", "type": "home" if home else "road", "summary": "0-0"},
                     {"name": "vs. Conf.", "type": "vsconf", "summary": "0-0"}]}
    if score is not None:
        c["score"] = {"value": float(score), "displayValue": str(score)} if schedule else str(score)
    if schedule:
        c["record"] = c.pop("records")
    return c


def event(eid: int, date: str, away: str, home: str, state: str, scores: tuple[int, int] | None, records: tuple[str, str],
          period: int = 0, clock: str = "0:00", situation: dict | None = None, week: int = WEEK, half: bool = False,
          schedule: bool = False, neutral: bool = False) -> dict:
    a_s, h_s = scores if scores else (None, None)
    st = status(state, period, clock, "STATUS_HALFTIME" if half else None)
    comp = {"id": str(eid), "uid": f"s:20~l:23~e:{eid}~c:{eid}", "date": date, "attendance": 0, "timeValid": True,
            "neutralSite": neutral, "conferenceCompetition": CONF_OF[away] == CONF_OF[home] and CONF_OF[away] != "Ind",
            "competitors": [competitor(home, True, h_s, records[1], winner=bool(scores and h_s > a_s), order=0, schedule=schedule),
                            competitor(away, False, a_s, records[0], winner=bool(scores and a_s > h_s), order=1, schedule=schedule)],
            "status": st}
    if situation:
        comp["situation"] = situation
    ev = {"id": str(eid), "uid": f"s:20~l:23~e:{eid}", "date": date, "name": f"{TEAMS[away][0]} {TEAMS[away][1]} at {TEAMS[home][0]} {TEAMS[home][1]}",
          "shortName": f"{away} @ {home}", "week": {"number": week}, "competitions": [comp], "status": st}
    if schedule:
        ev["season"] = {"year": SEASON, "displayName": str(SEASON)}
        ev["seasonType"] = {"id": "2", "type": 2, "name": "Regular Season", "abbreviation": "reg"}
        ev["week"]["text"] = f"Week {week}"
    else:
        ev["season"] = {"year": SEASON, "type": 2, "slug": "regular-season"}
    return ev


def scoreboard() -> dict:
    live_sit = {"lastPlay": {"id": "1", "text": "Bryce Underwood pass complete to Donaven McCulley for 14 yds", "team": {"id": str(team_id("MICH"))}},
                "down": 2, "yardLine": 18, "distance": 7, "downDistanceText": "2nd & 7 at OU 18", "shortDownDistanceText": "2nd & 7",
                "possessionText": "OU 18", "isRedZone": True, "homeTimeouts": 3, "awayTimeouts": 2, "possession": str(team_id("MICH"))}
    events = [
        event(401756001, "2026-09-04T23:30Z", "GT", "CLEM", "post", (17, 31), ("1-1", "2-0"), period=4),
        event(401756002, "2026-09-05T00:00Z", "MEM", "ARK", "post", (24, 27), ("1-1", "2-0"), period=5),
        event(401756003, "2026-09-05T16:00Z", "MICH", "OU", "in", (14, 10), ("1-0", "1-0"), period=3, clock="7:12", situation=live_sit),
        event(401756004, "2026-09-05T16:00Z", "BAY", "SMU", "in", (7, 7), ("1-0", "1-0"), period=2, clock="0:00", half=True),
        event(401756005, "2026-09-05T19:30Z", "ISU", "IOWA", "pre", None, ("2-0", "1-0")),
        event(401756006, "2026-09-05T19:30Z", "UTAH", "BYU", "pre", None, ("1-0", "1-0")),
        event(401756007, "2026-09-05T23:30Z", "TEX", "OSU", "pre", None, ("1-0", "1-0"), neutral=False),
        event(401756008, "2026-09-05T23:30Z", "ALA", "FSU", "pre", None, ("1-0", "0-1")),
        event(401756009, "2026-09-05T20:00Z", "TROY", "APP", "pre", None, ("1-0", "1-0")),
        event(401756010, "2026-09-05T21:00Z", "ODU", "JMU", "pre", None, ("0-1", "1-0")),
        event(401756011, "2026-09-05T23:00Z", "NIU", "MASS", "pre", None, ("1-0", "0-1")),
        event(401756012, "2026-09-06T02:30Z", "UNLV", "BSU", "pre", None, ("1-0", "1-0")),
    ]
    return {"leagues": [{"id": "23", "uid": "s:20~l:23", "name": "NCAA - Football", "abbreviation": "NCAAF", "slug": "college-football",
                         "season": {"year": SEASON, "startDate": f"{SEASON}-08-01T07:00Z", "endDate": f"{SEASON + 1}-01-20T07:59Z",
                                    "type": {"id": "2", "type": 2, "name": "Regular Season", "abbreviation": "reg"}}}],
            "season": {"type": 2, "year": SEASON}, "week": {"number": WEEK}, "events": events}


def stat(name: str, value: float, display: str, kind: str = "total") -> dict:
    return {"name": name, "abbreviation": name.upper(), "type": kind, "value": value, "displayValue": display}


def entry(abbrev: str, rnd: random.Random) -> dict:
    wins = rnd.choice([0, 1, 1, 1, 2, 2]); losses = rnd.choice([0, 0, 1]) if wins < 2 else 0
    if abbrev in RANK_OF:
        wins, losses = 2 - (RANK_OF[abbrev] > 20), 1 if RANK_OF[abbrev] > 20 else 0
    cw, cl = (1, 0) if wins and rnd.random() < 0.3 else (0, 1) if losses and rnd.random() < 0.3 else (0, 0)
    played = wins + losses
    pct = wins / played if played else 0.0
    return {"team": team(abbrev, full=False), "note": {}, "stats": [
        stat("wins", wins, str(wins)), stat("losses", losses, str(losses)),
        stat("winpercent", pct, f"{pct:.3f}"), stat("overall", 0, f"{wins}-{losses}"),
        stat("streak", wins if wins else -losses, f"W{wins}" if wins else f"L{losses}" if losses else "-"),
        stat("pointsfor", 60, "60"), stat("pointsagainst", 30, "30"), stat("differential", 30, "+30"),
        stat("vsconf", 0, f"{cw}-{cl}", "vsconf"), stat("vsconf_wins", cw, str(cw), "vsconf"), stat("vsconf_losses", cl, str(cl), "vsconf"),
        stat("vsconf_winpercent", cw / (cw + cl) if cw + cl else 0.0, f"{cw / (cw + cl) if cw + cl else 0:.3f}", "vsconf"),
        stat("vsconf_gamesbehind", 0, "-", "vsconf"), stat("gamesbehind", 0, "-"), stat("playoffseed", 0, "0"),
    ]}


def standings() -> dict:
    rnd = random.Random(SEASON * WEEK)
    children = []
    for conf, (name, cid, members) in CONFERENCES.items():
        node = {"uid": f"s:20~l:23~g:{cid}", "id": str(cid), "name": name, "abbreviation": conf, "shortName": conf, "isConference": True}
        if conf in DIVISIONS:
            node["children"] = [{"uid": f"s:20~l:23~g:{cid}{i}", "id": f"{cid}{i}", "name": div, "abbreviation": div.split(" - ")[-1], "isConference": False,
                                 "standings": {"id": "0", "name": "overall", "displayName": "Standings", "entries": [entry(a, rnd) for a in ts]}}
                                for i, (div, ts) in enumerate(DIVISIONS[conf].items(), 1)]
        else:
            node["standings"] = {"id": "0", "name": "overall", "displayName": "Standings", "entries": [entry(a, rnd) for a in members]}
        children.append(node)
    return {"uid": "s:20~l:23", "id": "23", "name": "NCAA - Football", "abbreviation": "NCAAF", "shortName": "NCAAF",
            "children": children, "season": SEASON, "seasonType": 2}


def teams_list() -> dict:
    return {"sports": [{"id": "20", "uid": "s:20", "name": "Football", "slug": "football",
                        "leagues": [{"id": "23", "uid": "s:20~l:23", "name": "NCAA - Football", "abbreviation": "NCAAF", "shortName": "NCAAF", "slug": "college-football",
                                     "teams": [{"team": team(a)} for a in sorted(TEAMS)]}]}]}


def schedule_mich() -> dict:
    slate = [("2026-08-29T19:30Z", "NMSU", "MICH", (7, 45), ("0-1", "1-0"), 1),
             ("2026-09-05T16:00Z", "MICH", "OU", None, ("1-0", "1-0"), 2),
             ("2026-09-12T16:00Z", "CMU", "MICH", None, ("0-0", "0-0"), 3),
             ("2026-09-19T19:30Z", "MICH", "NEB", None, ("0-0", "0-0"), 4),
             ("2026-09-26T16:00Z", "WIS", "MICH", None, ("0-0", "0-0"), 5),
             ("2026-10-03T16:00Z", "MICH", "USC", None, ("0-0", "0-0"), 6),
             ("2026-10-17T16:00Z", "WASH", "MICH", None, ("0-0", "0-0"), 8),
             ("2026-10-24T23:30Z", "MICH", "MSU", None, ("0-0", "0-0"), 9),
             ("2026-11-01T00:00Z", "PUR", "MICH", None, ("0-0", "0-0"), 10),
             ("2026-11-07T17:00Z", "MICH", "NW", None, ("0-0", "0-0"), 11),
             ("2026-11-14T17:00Z", "MD", "MICH", None, ("0-0", "0-0"), 12),
             ("2026-11-21T20:30Z", "MICH", "PSU", None, ("0-0", "0-0"), 13),
             ("2026-11-28T17:00Z", "OSU", "MICH", None, ("0-0", "0-0"), 14)]
    events = []
    for i, (date, away, home, scores, records, week) in enumerate(slate):
        state = "post" if scores else "pre"
        events.append(event(401755900 + i, date, away, home, state, scores, records, period=4 if scores else 0, week=week, schedule=True))
    return {"timestamp": f"{TODAY}T15:00Z", "status": "success", "season": {"year": SEASON, "type": 2, "name": "Regular Season", "displayName": str(SEASON)},
            "team": team("MICH"), "events": events, "requestedSeason": {"year": SEASON, "type": 2, "name": "Regular Season", "displayName": str(SEASON)}}


def main() -> None:
    for name, payload in (("espn_scoreboard.json", scoreboard()), ("espn_standings.json", standings()),
                          ("espn_teams.json", teams_list()), ("espn_schedule_MICH.json", schedule_mich())):
        (HERE / name).write_text(json.dumps(payload, indent=None if name == "espn_standings.json" else 1) + "\n")
        print(name)


if __name__ == "__main__":
    main()
