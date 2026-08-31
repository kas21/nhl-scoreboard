"""Who may talk to the web UI.

The API has no login. For a sign on a home network that is a fair trade — a password
on your own scoreboard is daily friction with little to buy it — but it only holds if
a *browser* cannot be tricked into driving the API on behalf of whoever is sitting in
front of it. Two doors are shut here; nothing else about the API changes.

**Drive-by CSRF.** Four state-changing endpoints take no request body, which makes
them CORS "simple requests": a page on any other site can POST them with no preflight,
and the action runs even though the reply is opaque to the attacker. ``/api/config/reset``
wipes the configuration; ``/api/system/update`` pulls and pip-installs as root. Demanding
a header that a cross-origin ``fetch`` cannot set without a preflight — one this app
never answers, since it sends no CORS headers at all — closes that off.

**DNS rebinding.** A name the attacker controls, re-pointed at the Pi, makes their page
same-origin: every CORS rule stops applying, so they could set the header above freely.
Pinning ``Host`` to the names the scoreboard actually answers to closes that. Literal
addresses stay allowed, because rebinding is an attack on names — you cannot rebind
``192.168.1.42``.

Neither check is authentication. Someone who can send raw HTTP to the port still has
full control, which is the documented trust boundary: put the scoreboard on a network
you trust, and do not port-forward it.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger(__name__)

UI_HEADER = "x-requested-with"
UI_TOKEN = "scoreboard-ui"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain"})
WS_POLICY_VIOLATION = 1008


def hostname_of(value: str) -> str:
    """The bare host from a ``Host`` header or an origin: no port, no brackets, no trailing dot."""
    host = value.strip().lower().rstrip(".")
    if host.startswith("["):                       # [::1]:8080 — bracketed IPv6
        return host.partition("]")[0][1:]
    return host.rpartition(":")[0] if host.count(":") == 1 else host


def is_address(host: str) -> bool:
    """True for a literal IP. Those are safe to accept: rebinding needs a name to re-point."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def machine_names() -> set[str]:
    """What this box answers to: its short hostname, and the mDNS name that follows from it.

    Read fresh each time rather than cached, because ``/api/system/hostname`` can change
    it while the server is running and the new name has to work straight away.
    """
    try:
        short = socket.gethostname().lower().rstrip(".").partition(".")[0]
    except OSError:
        return set()
    return {short, f"{short}.local"} if short else set()


class AccessGuard:
    """ASGI middleware enforcing the two rules above on HTTP *and* WebSocket traffic.

    Plain ASGI rather than ``BaseHTTPMiddleware`` so the preview socket — a live picture
    of the panel — is covered by the same host check as everything else.
    """

    def __init__(self, app: ASGIApp, extra_hosts: Callable[[], list[str]] = list) -> None:
        self.app = app
        self._extra_hosts = extra_hosts

    def allowed_hosts(self) -> set[str]:
        extra = {h.strip().lower().rstrip(".") for h in self._extra_hosts() if h and h.strip()}
        return LOCAL_NAMES | machine_names() | extra

    def host_allowed(self, host: str) -> bool:
        return not host or is_address(host) or host in self.allowed_hosts()

    def refusal(self, scope: Scope, headers: Headers) -> str | None:
        """Why this request must not be served, or None to let it through."""
        host = hostname_of(headers.get("host", ""))
        if not self.host_allowed(host):
            return f"Host {host!r} is not a name this scoreboard answers to"

        origin = headers.get("origin")
        if origin and origin != "null" and not self.host_allowed(hostname_of(urlsplit(origin).netloc)):
            return f"cross-origin request from {origin!r}"

        if scope["type"] == "websocket" or scope.get("method", "GET") in SAFE_METHODS:
            return None
        if headers.get(UI_HEADER, "").strip().lower() != UI_TOKEN:
            return f"state-changing {scope.get('method')} without the {UI_HEADER} header"
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        reason = self.refusal(scope, Headers(scope=scope))
        if reason is None:
            await self.app(scope, receive, send)
            return
        log.warning("refused %s %s: %s", scope["type"], scope.get("path", ""), reason)
        await (_close_socket(send) if scope["type"] == "websocket" else _forbid(send, reason))


async def _forbid(send: Send, reason: str) -> None:
    body = f"403 Forbidden: {reason}\n".encode()
    await send({"type": "http.response.start", "status": 403,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def _close_socket(send: Send) -> None:
    await send({"type": "websocket.close", "code": WS_POLICY_VIOLATION})
