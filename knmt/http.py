"""Shared HTTP session with a polite User-Agent and simple retry."""
from __future__ import annotations

import time

import requests

USER_AGENT = (
    "knmt-watch/1.0 (personal vacancy monitor; "
    "https://github.com/; respectful daily polling)"
)

_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "nl,en;q=0.8"})
        _session = s
    return _session


def get(url: str, *, params=None, retries: int = 3, timeout: int = 30) -> requests.Response:
    """GET with a few retries on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = session().get(url, params=params, timeout=timeout)
            if resp.status_code >= 500:
                raise requests.HTTPError(f"{resp.status_code} server error")
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001 - transient network/HTTP
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    assert last_exc is not None
    raise last_exc
