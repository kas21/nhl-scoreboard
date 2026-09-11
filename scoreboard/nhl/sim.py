"""A hockey game you run by hand: the NHL simulation engine.

Publishes ``nhl.main_event`` (and the game on top of ``nhl.scores``) in exactly the shape
``normalize.py`` produces from the real feed, so the state machine, the goal and penalty
detectors and every NHL board see a real game. The clock runs on the hub's tick;
everything else — goals, penalties, pulled goalies, period ends — is a button.

Rules kept, because the boards react to them: a minor ends early when the team on the
power play scores; two men down is the floor (five on three); a pulled goalie adds a
skater and shows as an extra-attacker "power play" the way the feed's situation code
does; a tie after the third goes to a five-minute overtime and then a shootout in the
regular season, twenty-minute overtimes until someone scores in the playoffs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, timedelta
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..sim.base import Action, Param, SimContext, SimError
from .normalize import period_label
from .teams import NHL_TEAMS, TEAM_NAMES, team

TeamAbbrev = Literal[NHL_TEAMS]  # type: ignore[valid-type]
Side = Literal["away", "home"]

SIM_GAME_ID = 2099990001            # out of the real id range; the dashboard marks it "on panel" like any other
REGULATION_PERIODS = 3
REGULAR_OT_SECONDS = 5 * 60
PLAYOFF_OT_SECONDS = 20 * 60
INTERMISSION_SECONDS = 18 * 60
CRIT_SECONDS = 5 * 60               # the feed flips LIVE -> CRIT late in a close game
MIN_SKATERS = 3
GAME_TYPE = {"preseason": 1, "regular": 2, "playoff": 3}

# Stand-in players, so a goal card has a name and a number on it. Cycled per team.
ROSTER: tuple[tuple[str, int], ...] = (
    ("Alex Rivera", 91), ("Sam Okafor", 34), ("Jordan Lee", 88), ("Max Dubois", 16),
    ("Chris Novak", 27), ("Taylor Berg", 44), ("Dev Patel", 9), ("Nico Laine", 71),
)
TEAM_LABELS = {a: f"{a} — {c} {n}".strip() for a, (c, n) in TEAM_NAMES.items()}
PENALTY_KINDS: tuple[tuple[str, str], ...] = (
    ("tripping", "Tripping"), ("hooking", "Hooking"), ("slashing", "Slashing"), ("holding", "Holding"),
    ("interference", "Interference"), ("high sticking", "High-sticking"), ("cross checking", "Cross-checking"),
    ("roughing", "Roughing"), ("delay of game", "Delay of game"), ("too many men", "Too many men"),
    ("fighting", "Fighting"),
)


class NhlSimOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", title="NHL game")
    away: TeamAbbrev = Field("MTL", description="Visiting team", json_schema_extra={"labels": TEAM_LABELS})
    home: TeamAbbrev = Field("TOR", description="Home team", json_schema_extra={"labels": TEAM_LABELS})
    favorite: Literal["auto", "away", "home", "none"] = Field(
        "auto", description="Whose goals get the celebration: auto follows your NHL favourites, none plays every goal as an opponent's")
    game_type: Literal["preseason", "regular", "playoff"] = "regular"
    period_minutes: int = Field(20, ge=1, le=20, description="Length of a period; shorter is handy for a quick run-through")
    speed: float = Field(1.0, ge=0.25, le=60, description="Clock multiplier: 10 runs a period in two minutes")
    start_in: Literal["pregame", "live"] = Field("pregame", description="Begin before the game, or with the puck dropped")
    starts_in_minutes: int = Field(30, ge=0, le=600, description="Pregame: how far off the start time reads")


@dataclass
class _Penalty:
    side: str
    left: float                 # game seconds remaining on it
    duration: int               # minutes as called
    major: bool
    record: dict[str, Any]      # the entry that goes into game["penalties"]


@dataclass
class _Goal:
    side: str
    record: dict[str, Any]


@dataclass
class _Game:
    """Everything mutable about the game in play."""

    period: int = 0                     # 0 before the puck drops
    remaining: float = 0.0              # seconds left in the period (or the intermission)
    running: bool = False
    intermission: bool = False
    shootout: bool = False
    final: bool = False
    score: dict[str, int] = field(default_factory=lambda: {"away": 0, "home": 0})
    sog: dict[str, int] = field(default_factory=lambda: {"away": 0, "home": 0})
    pulled: dict[str, bool] = field(default_factory=lambda: {"away": False, "home": False})
    goals: list[_Goal] = field(default_factory=list)
    penalties: list[_Penalty] = field(default_factory=list)         # active
    penalty_log: list[dict[str, Any]] = field(default_factory=list)   # every one called, for game["penalties"]
    roster_cursor: dict[str, int] = field(default_factory=lambda: {"away": 0, "home": 0})
    last_tick: float = 0.0
    last_period_type: str = "REG"       # what ended the game, for the outcome label


class NhlSim:
    key: ClassVar[str] = "nhl"
    title: ClassVar[str] = "NHL game"
    description: ClassVar[str] = ("A game between any two teams, on your clock: start and stop it, score, call penalties, "
                                  "pull a goalie, end periods. Goal and penalty alerts fire exactly as they would on a real night.")
    options_model: ClassVar[type[BaseModel]] = NhlSimOptions
    claims: ClassVar[frozenset[str]] = frozenset({"nhl.main_event", "nhl.scores"})

    def __init__(self) -> None:
        self.opts = NhlSimOptions()
        self.g = _Game()
        self._records: dict[str, str] = {}
        self._slate: list[dict[str, Any]] = []
        self._favorite_side: str | None = None
        self._date = ""
        self._start_utc = ""
        self._values: dict[str, Any] = {}

    # -- lifecycle ---------------------------------------------------------------

    def start(self, options: NhlSimOptions, ctx: SimContext) -> None:  # type: ignore[override]
        if options.away == options.home:
            raise SimError("pick two different teams")
        self.opts = options
        self.g = _Game(last_tick=ctx.now)
        snap = ctx.snapshot
        rows = (snap.get("nhl.standings") or {}).get("teams") or {}
        self._records = {a: f"{r['wins']}-{r['losses']}-{r['otl']}" for a, r in rows.items() if a in (options.away, options.home)}
        self._slate = [g for g in (snap.get("nhl.scores") or []) if g.get("id") != SIM_GAME_ID]
        self._favorite_side = self._pick_favorite(options, ctx)
        self._date = ctx.wall.date().isoformat()
        start = ctx.wall + timedelta(minutes=options.starts_in_minutes if options.start_in == "pregame" else 0)
        self._start_utc = start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if options.start_in == "live":
            self._puck_drop(ctx)
        self._rebuild()

    def _pick_favorite(self, options: NhlSimOptions, ctx: SimContext) -> str | None:
        if options.favorite in ("away", "home"):
            return options.favorite
        if options.favorite == "none":
            return None
        favs = [str(f).upper() for f in (ctx.config.sources.get("nhl") or {}).get("favorites") or ["TOR"]]
        for fav in favs:                      # highest-priority favourite in the game wins
            for side in ("away", "home"):
                if getattr(options, side) == fav:
                    return side
        return None

    # -- ticking ------------------------------------------------------------------

    def tick(self, ctx: SimContext) -> bool:
        g = self.g
        elapsed = max(ctx.now - g.last_tick, 0.0) * self.opts.speed
        g.last_tick = ctx.now
        if not g.running or g.final or elapsed <= 0:
            return False
        before = self._values
        if g.intermission:
            g.remaining = max(g.remaining - elapsed, 0.0)
            if g.remaining <= 0:
                self._begin_period(g.period + 1)      # the intermission ran out: next period, clock stopped
        else:
            step = min(elapsed, g.remaining)
            g.remaining -= step
            self._run_penalties(step)
            if g.remaining <= 0:
                self._period_over()
        self._rebuild()
        return self._values != before

    def _run_penalties(self, seconds: float) -> None:
        for p in self.g.penalties:
            p.left -= seconds
        self.g.penalties = [p for p in self.g.penalties if p.left > 0]

    # -- actions ------------------------------------------------------------------

    def actions(self) -> list[Action]:
        g, o = self.g, self.opts
        sides = ((("away", o.away), ("home", o.home)))
        side_param = Param("side", "Team", "select", sides, default="home")
        out: list[Action] = []
        if g.final:
            out.append(Action("reset", "New game", "Game", primary=True, hint="Back to pregame with the same teams"))
            return out
        if g.period == 0:
            out.append(Action("puck_drop", "Drop the puck", "Clock", primary=True))
        elif g.shootout:
            out.append(Action("goal", "Shootout winner", "Scoring", (side_param,), primary=True,
                              hint="Scores the deciding goal and ends the game"))
        elif g.intermission:
            out.append(Action("next_period", self._next_period_label(), "Clock", primary=True))
            out.append(Action("clock", "Pause intermission" if g.running else "Resume intermission", "Clock"))
        else:
            out.append(Action("clock", "Stop clock" if g.running else "Start clock", "Clock", primary=True))
            out.append(Action("set_clock", "Set", "Clock", (Param("clock", "Clock", "text", default=_mmss(g.remaining), placeholder="mm:ss"),)))
            out.append(Action("end_period", "End period" if g.period <= REGULATION_PERIODS or self._tied() else "End game", "Clock",
                              hint="Runs the clock out"))
            out.append(Action("goal", "Goal!", "Scoring", (
                side_param,
                Param("scorer", "Scorer", "text", placeholder="blank = a stand-in"),
                Param("assists", "Assists", "text", placeholder="comma separated"),
            ), primary=True))
            out.append(Action("shot", "Shot on goal", "Scoring", (side_param,)))
            out.append(Action("overturn", "Overturn last goal", "Scoring", (side_param,),
                              enabled=bool(g.goals), hint="Takes the side's last goal off the board"))
            out.append(Action("penalty", "Penalty", "Penalties", (
                side_param,
                Param("minutes", "Minutes", "select", (("2", "2 min minor"), ("4", "4 min double minor"), ("5", "5 min major")), default="2"),
                Param("kind", "Infraction", "select", PENALTY_KINDS, default="tripping"),
                Param("player", "Player", "text", placeholder="blank = a stand-in"),
            ), primary=True))
            out.append(Action("clear_penalties", "Clear penalties", "Penalties", enabled=bool(g.penalties)))
            for side, abbrev in sides:
                out.append(Action(f"pull_{side}", f"{'Return' if g.pulled[side] else 'Pull'} {abbrev} goalie", "Situation"))
        if g.period > 0:
            out.append(Action("final", "Go to final", "Game", danger=True, hint="Ends the game as it stands (a tie goes to the home side)"))
        out.append(Action("reset", "Reset", "Game", danger=True, hint="Back to pregame with the same teams"))
        return out

    def action(self, name: str, params: dict[str, Any], ctx: SimContext) -> None:
        g = self.g
        self.tick(ctx)                                    # settle the clock up to now first
        if name == "reset":
            self.start(self.opts.model_copy(update={"start_in": "pregame"}), ctx)
            return
        if g.final:
            raise SimError("the game is over; start a new one")
        handler = getattr(self, f"_do_{name}", None)
        if handler is None:
            raise SimError(f"unknown action {name!r}")
        handler(params, ctx)
        self._rebuild()

    def _do_puck_drop(self, params: dict[str, Any], ctx: SimContext) -> None:
        if self.g.period != 0:
            raise SimError("the game has already started")
        self._puck_drop(ctx)

    def _do_clock(self, params: dict[str, Any], ctx: SimContext) -> None:
        self._need_started()
        self.g.running = not self.g.running

    def _do_set_clock(self, params: dict[str, Any], ctx: SimContext) -> None:
        self._need_live()
        self.g.remaining = float(min(_parse_mmss(str(params.get("clock", ""))), self._period_seconds(self.g.period)))

    def _do_end_period(self, params: dict[str, Any], ctx: SimContext) -> None:
        self._need_live()
        self._run_penalties(self.g.remaining)
        self.g.remaining = 0.0
        self._period_over()

    def _do_next_period(self, params: dict[str, Any], ctx: SimContext) -> None:
        if not self.g.intermission:
            raise SimError("not in an intermission")
        self._begin_period(self.g.period + 1)

    def _do_goal(self, params: dict[str, Any], ctx: SimContext) -> None:
        g = self.g
        self._need_started()
        if g.intermission:
            raise SimError("no goals during an intermission; start the next period first")
        side = _side(params)
        other = "home" if side == "away" else "away"
        strength = self._strength(side)
        scorer = str(params.get("scorer") or "").strip()
        sweater = 0
        if not scorer:
            scorer, sweater = self._next_player(side)
        first, _, last = scorer.partition(" ")
        assists = [a.strip() for a in str(params.get("assists") or "").split(",") if a.strip()]
        g.score[side] += 1
        g.sog[side] += 1
        abbrev = getattr(self.opts, side)
        record = {
            "team": abbrev, "period": g.period, "time": _mmss(self._period_seconds(g.period) - g.remaining),
            "scorer": scorer, "first_name": first, "last_name": last, "sweater": sweater,
            "goals_to_date": 1 + sum(1 for x in g.goals if x.record["scorer"] == scorer and x.side == side),
            "strength": strength, "assists": assists[:2],
            "away_score": g.score["away"], "home_score": g.score["home"],
        }
        g.goals.append(_Goal(side, record))
        if strength == "pp":                         # a power-play goal ends the other side's earliest minor
            minors = sorted((p for p in g.penalties if p.side == other and not p.major), key=lambda p: p.left)
            if minors:
                g.penalties.remove(minors[0])
        if g.shootout or g.period > REGULATION_PERIODS:
            self._finish()

    def _do_shot(self, params: dict[str, Any], ctx: SimContext) -> None:
        self._need_live()
        self.g.sog[_side(params)] += 1

    def _do_overturn(self, params: dict[str, Any], ctx: SimContext) -> None:
        g = self.g
        self._need_started()
        side = _side(params)
        for i in range(len(g.goals) - 1, -1, -1):
            if g.goals[i].side == side:
                del g.goals[i]
                g.score[side] -= 1
                return
        raise SimError(f"{getattr(self.opts, side)} has no goal to overturn")

    def _do_penalty(self, params: dict[str, Any], ctx: SimContext) -> None:
        g = self.g
        self._need_live()
        side = _side(params)
        minutes = int(params.get("minutes") or 2)
        if minutes not in (2, 4, 5):
            raise SimError("a penalty is 2, 4 or 5 minutes")
        kind = str(params.get("kind") or "tripping")
        player = str(params.get("player") or "").strip() or self._next_player(side)[0]
        record = {
            "team": getattr(self.opts, side), "period": g.period,
            "time": _mmss(self._period_seconds(g.period) - g.remaining),
            "type": "MAJ" if minutes == 5 else "MIN", "duration": minutes, "desc": kind, "player": player,
        }
        g.penalty_log.append(record)
        g.penalties.append(_Penalty(side, float(minutes * 60), minutes, minutes == 5, record))

    def _do_clear_penalties(self, params: dict[str, Any], ctx: SimContext) -> None:
        self.g.penalties = []

    def _do_pull_away(self, params: dict[str, Any], ctx: SimContext) -> None:
        self._need_live()
        self.g.pulled["away"] = not self.g.pulled["away"]

    def _do_pull_home(self, params: dict[str, Any], ctx: SimContext) -> None:
        self._need_live()
        self.g.pulled["home"] = not self.g.pulled["home"]

    def _do_final(self, params: dict[str, Any], ctx: SimContext) -> None:
        self._need_started()
        if self._tied():
            self.g.score["home"] += 1                   # someone has to win
        self._finish()

    # -- game flow ----------------------------------------------------------------

    def _need_started(self) -> None:
        if self.g.period == 0:
            raise SimError("drop the puck first")

    def _need_live(self) -> None:
        self._need_started()
        if self.g.intermission:
            raise SimError("not during an intermission")
        if self.g.shootout:
            raise SimError("not during a shootout; pick the shootout winner")

    def _puck_drop(self, ctx: SimContext) -> None:
        self.g.last_tick = ctx.now
        self._begin_period(1)
        self.g.running = True

    def _begin_period(self, number: int) -> None:
        g = self.g
        g.period = number
        g.intermission = False
        g.running = False
        g.pulled = {"away": False, "home": False}
        if number > REGULATION_PERIODS and self.opts.game_type != "playoff" and number > REGULATION_PERIODS + 1:
            g.shootout = True                    # regular season: one OT, then the shootout
            g.remaining = 0.0
            g.penalties = []
            return
        g.remaining = float(self._period_seconds(number))

    def _period_over(self) -> None:
        """The clock ran out: intermission, overtime, shootout or final, by the rules."""
        g = self.g
        g.running = False
        if g.period >= REGULATION_PERIODS and not self._tied():
            self._finish()
            return
        g.intermission = True
        g.remaining = float(INTERMISSION_SECONDS)
        g.running = True                          # the intermission clock counts down on its own

    def _finish(self) -> None:
        g = self.g
        g.final = True
        g.running = False
        g.intermission = False
        g.pulled = {"away": False, "home": False}
        g.penalties = []
        g.last_period_type = "SO" if g.shootout else ("OT" if g.period > REGULATION_PERIODS else "REG")

    def _tied(self) -> bool:
        return self.g.score["away"] == self.g.score["home"]

    def _period_seconds(self, number: int) -> int:
        if number <= REGULATION_PERIODS:
            return self.opts.period_minutes * 60
        return PLAYOFF_OT_SECONDS if self.opts.game_type == "playoff" else REGULAR_OT_SECONDS

    def _next_period_label(self) -> str:
        n = self.g.period + 1
        if n <= REGULATION_PERIODS:
            return f"Start {period_label({'number': n, 'periodType': 'REG'}, self._game_type())} period"
        if self.opts.game_type != "playoff" and n > REGULATION_PERIODS + 1:
            return "Go to shootout"
        return "Start overtime"

    def _game_type(self) -> int:
        return GAME_TYPE[self.opts.game_type]

    def _next_player(self, side: str) -> tuple[str, int]:
        i = self.g.roster_cursor[side]
        self.g.roster_cursor[side] = i + 1
        return ROSTER[i % len(ROSTER)]

    def _skaters(self, side: str) -> int:
        down = sum(1 for p in self.g.penalties if p.side == side)
        return max(5 - down, MIN_SKATERS) + (1 if self.g.pulled[side] else 0)

    def _strength(self, side: str) -> str:
        other = "home" if side == "away" else "away"
        a, b = self._skaters(side), self._skaters(other)
        return "pp" if a > b else "sh" if a < b else "ev"

    # -- output -------------------------------------------------------------------

    def _rebuild(self) -> None:
        game = self._game_dict()
        self._values = {"nhl.main_event": game, "nhl.scores": [game, *self._slate]}

    def values(self) -> dict[str, Any]:
        return self._values

    def _game_dict(self) -> dict[str, Any]:
        g = self.g
        gtype = self._game_type()
        if g.final:
            state, phase = "OVER", "postgame"
        elif g.period == 0:
            state, phase = "FUT", "pregame"
        elif g.intermission:
            state, phase = "LIVE", "intermission"
        else:
            crit = g.period >= REGULATION_PERIODS and g.remaining <= CRIT_SECONDS and abs(g.score["away"] - g.score["home"]) <= 1
            state, phase = ("CRIT" if crit else "LIVE"), "live"
        ptype = "SO" if g.shootout else ("OT" if g.period > REGULATION_PERIODS else "REG")
        descriptor = {"number": g.period, "periodType": ptype, "maxRegulationPeriods": REGULATION_PERIODS} if g.period else None
        outcome = ""
        if g.final:
            ot_n = g.period - REGULATION_PERIODS
            outcome = ("FINAL/SO" if g.last_period_type == "SO"
                       else f"FINAL/{ot_n}OT" if g.last_period_type == "OT" and ot_n > 1
                       else "FINAL/OT" if g.last_period_type == "OT" else "FINAL")
        a_sk, h_sk = self._skaters("away"), self._skaters("home")
        if g.period == 0 or g.final or g.shootout:
            a_sk = h_sk = 5
        pp_code = f"a{a_sk}{h_sk}" if a_sk > h_sk else f"h{h_sk}{a_sk}" if h_sk > a_sk else "ev"
        short = "away" if a_sk < h_sk else "home" if h_sk < a_sk else None
        theirs = [p.left for p in g.penalties if p.side == short] if short else []
        pp_clock = _mmss(min(theirs)) if theirs else ""
        pulled = (1 if g.pulled["away"] else 0) | (2 if g.pulled["home"] else 0)
        return {
            "id": SIM_GAME_ID, "type": gtype, "state": state, "phase": phase,
            "date": self._date, "start_time_utc": self._start_utc,
            "away": self._team("away"), "home": self._team("home"),
            "period": period_label(descriptor, gtype), "period_number": g.period,
            "clock": _mmss(g.remaining) if g.period else "",
            "clock_running": g.running and not g.final,
            "in_intermission": g.intermission,
            "outcome": outcome,
            "powerplay": {"code": pp_code, "clock": pp_clock},
            "pulled_goalie": pulled,
            "goals": [x.record for x in g.goals],
            "penalties": list(g.penalty_log),
            "favorite_side": self._favorite_side,
            "sport": "nhl",
            "simulated": True,
        }

    def _team(self, side: str) -> dict[str, Any]:
        abbrev = getattr(self.opts, side)
        t = team(abbrev)
        return {"abbrev": abbrev, "name": t.name, "city": t.city, "score": self.g.score[side],
                "sog": self.g.sog[side], "record": self._records.get(abbrev, "")}

    def describe(self) -> dict[str, Any]:
        g, o = self.g, self.opts
        game = self._values.get("nhl.main_event") or self._game_dict()
        clock = game["clock"] + (" ▶" if game["clock_running"] else " ⏸") if game["clock"] else "—"
        pp = game["powerplay"]
        active = [f"{p.record['team']} {p.duration}:00 {p.record['desc']} ({_mmss(p.left)} left)" for p in g.penalties]
        lines = [
            ["Phase", game["outcome"] or game["phase"]],
            ["Period", ("INT after " if g.intermission else "") + (game["period"] or "—")],
            ["Clock", clock],
            ["Shots", f"{g.sog['away']} - {g.sog['home']}"],
            ["Situation", "even strength" if pp["code"] == "ev" else f"{pp['code']}{(' · ' + pp['clock']) if pp['clock'] else ''}"],
            ["Penalties", "; ".join(active) if active else "none active"],
            ["Empty net", ", ".join(getattr(o, s) for s in ("away", "home") if g.pulled[s]) or "no"],
            ["Favourite", getattr(o, self._favorite_side) if self._favorite_side else "none"],
        ]
        return {"headline": f"{o.away} {g.score['away']} - {g.score['home']} {o.home}", "lines": lines}


# -- helpers ----------------------------------------------------------------------

def _mmss(seconds: float) -> str:
    s = max(int(round(seconds)), 0)
    return f"{s // 60:02d}:{s % 60:02d}"


def _parse_mmss(text: str) -> int:
    text = text.strip()
    try:
        if ":" in text:
            m, s = text.split(":", 1)
            value = int(m) * 60 + int(s)
        else:
            value = int(float(text) * 60)
    except ValueError as exc:
        raise SimError(f"clock must read mm:ss, not {text!r}") from exc
    if value < 0:
        raise SimError("clock cannot be negative")
    return value


def _side(params: dict[str, Any]) -> str:
    side = str(params.get("side") or "home")
    if side not in ("away", "home"):
        raise SimError("side must be away or home")
    return side
