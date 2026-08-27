"""Shared quirks of ESPN's public site API.

``site.api.espn.com`` allowlists recognised HTTP-client user agents and answers 403 to
anything else — the app-wide descriptive one included. Requests to it therefore go out
under httpx's own identity.

The app-wide user agent cannot simply be changed to match: adsb.lol wants the opposite,
answering 403 to the library default and 200 to a descriptive name. Hence a per-request
override here rather than a different default on the shared client.
"""
from __future__ import annotations

import httpx

API_UA = f"python-httpx/{httpx.__version__}"
HEADERS = {"User-Agent": API_UA}
