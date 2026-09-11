"""The simulation contract.

A simulation stands in for a data source: it *claims* the snapshot keys that source
would publish and writes plausible values there under your control — a game whose
clock you start and stop, whose goals you score from a browser. Everything downstream
(the arbiter, the detectors, the director, the goal board) sees ordinary data and runs
exactly as it would on a real game night, which is the point: it is the boards and the
interrupts being exercised, not a mock of them.

One engine per feed. ``nhl/sim.py`` is the bundled one; a plugin registers its own under
the ``scoreboard.sims`` entry-point group, and a sport package that wants a simulator
implements this protocol next to its source. Nothing here is hockey-specific: a
weather-alert simulation would claim ``weather.alerts`` and offer "issue a warning" /
"clear" actions, and the same page would drive it.

Engines are plain objects driven from two threads (the hub's tick loop and the web
API); the hub serialises every call, so an engine needs no locking of its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from ..config import AppConfig
from ..data import Snapshot

ParamKind = Literal["select", "text", "number", "bool"]


class SimError(Exception):
    """An action that cannot be taken now (wrong phase, bad parameter). Reported to the UI, never fatal."""


@dataclass(frozen=True)
class Param:
    """One input an action takes; the page draws a control for it next to the button."""

    name: str
    label: str
    kind: ParamKind = "text"
    options: tuple[tuple[str, str], ...] = ()      # (value, label) for ``select``
    default: Any = None
    placeholder: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "kind": self.kind,
                "options": [list(o) for o in self.options], "default": self.default, "placeholder": self.placeholder}


@dataclass(frozen=True)
class Action:
    """A button. ``group`` clusters related buttons; ``primary`` is the one you most
    likely want next (the page draws it large); ``enabled`` False greys it out with ``hint``."""

    name: str
    label: str
    group: str = ""
    params: tuple[Param, ...] = ()
    enabled: bool = True
    primary: bool = False
    danger: bool = False
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "group": self.group, "params": [p.to_dict() for p in self.params],
                "enabled": self.enabled, "primary": self.primary, "danger": self.danger, "hint": self.hint}


@dataclass(frozen=True)
class SimContext:
    """What an engine may look at: a monotonic clock for pacing, the wall clock for dates
    and start times, the snapshot it is publishing into and the app config."""

    now: float                     # monotonic seconds; the only clock for pacing
    wall: datetime                 # tz-aware local time, for dates and start times
    snapshot: Snapshot
    config: AppConfig
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Simulation(Protocol):
    key: ClassVar[str]                           # "nhl": also the URL segment and the page's section
    title: ClassVar[str]
    description: ClassVar[str]
    options_model: ClassVar[type[BaseModel]]     # the start form; its JSON schema draws the page
    claims: ClassVar[frozenset[str]]             # snapshot keys taken over while running

    def start(self, options: BaseModel, ctx: SimContext) -> None:
        """Begin from ``options``. ``ctx.snapshot`` still holds the real data for the claimed
        keys, so an engine can build on it (records, the rest of the slate)."""
        ...

    def tick(self, ctx: SimContext) -> bool:
        """Advance to ``ctx.now``. Return True when ``values()`` changed and should be published."""
        ...

    def action(self, name: str, params: dict[str, Any], ctx: SimContext) -> None:
        """Apply a user action; raise ``SimError`` for one that makes no sense right now."""
        ...

    def actions(self) -> list[Action]:
        """The buttons that make sense in the current state, in display order."""
        ...

    def values(self) -> dict[str, Any]:
        """What to publish: every claimed key to its current value."""
        ...

    def describe(self) -> dict[str, Any]:
        """For the page: ``{"headline": str, "lines": [[label, value], ...]}``."""
        ...


def sim_schema(sim: Simulation) -> dict[str, Any]:
    """The options form's JSON schema, defaults included (pydantic leaves them out of
    ``required``-less fields only when they are None, so nothing to do beyond exporting)."""
    return sim.options_model.model_json_schema()
