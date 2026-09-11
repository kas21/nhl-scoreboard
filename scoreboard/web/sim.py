"""``/api/sim``: the Simulator page's endpoints.

Thin on purpose: the hub owns the rules and the engines own the game, and this only
turns their errors into status codes. One GET carries everything the page draws — every
engine's start form (its options model's JSON schema), and for a running one its
options, a summary and the buttons that apply right now — so the page can poll a single
URL once a second while a clock runs.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from ..data.store import ClaimError
from ..sim import SimError, SimulatorHub

CONFLICT = 409
UNPROCESSABLE = 422


def router(hub: SimulatorHub) -> APIRouter:
    api = APIRouter(prefix="/api/sim", tags=["simulator"])

    @api.get("")
    def state() -> dict[str, Any]:
        return hub.state()

    @api.post("/stop")
    def stop_all() -> dict[str, Any]:
        """Every simulation off and the real feeds back on the panel — the header badge's button."""
        return hub.stop_all()

    @api.post("/{key}/start")
    def start(key: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Start ``key`` with the form's values (restarts it if it is already running)."""
        try:
            return hub.start(key, options or {})
        except KeyError:
            raise HTTPException(status_code=404, detail=f"no simulation {key!r}") from None
        except ValidationError as exc:
            raise HTTPException(status_code=UNPROCESSABLE, detail=exc.errors()) from exc
        except SimError as exc:
            raise HTTPException(status_code=UNPROCESSABLE, detail=str(exc)) from exc
        except ClaimError as exc:
            raise HTTPException(status_code=CONFLICT, detail=str(exc)) from exc

    @api.post("/{key}/stop")
    def stop(key: str) -> dict[str, Any]:
        return hub.stop(key)

    @api.post("/{key}/action")
    def action(key: str, body: dict[str, Any]) -> dict[str, Any]:
        """A button press: ``{"name": ..., "params": {...}}``."""
        name = str(body.get("name") or "")
        params = body.get("params") or {}
        if not name or not isinstance(params, dict):
            raise HTTPException(status_code=UNPROCESSABLE, detail="an action needs a name and a params object")
        try:
            return hub.action(key, name, params)
        except KeyError as exc:
            raise HTTPException(status_code=CONFLICT, detail=str(exc.args[0]) if exc.args else "not running") from exc
        except SimError as exc:
            raise HTTPException(status_code=UNPROCESSABLE, detail=str(exc)) from exc

    return api
