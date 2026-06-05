"""City-level geocoding via OpenStreetMap Nominatim, cached by city name.

Locations on KNMT are city-level, so the cache stays tiny and we stay well within
Nominatim's usage policy (<=1 req/s, descriptive User-Agent, heavy caching).
"""
from __future__ import annotations

import time

from . import http

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_last_call = 0.0


def _cache_key(city: str, country: str) -> str:
    return f"{city.strip().lower()}|{(country or 'nl').lower()}"


def geocode_city(city: str, country: str, cache: dict) -> dict | None:
    """Return {'lat','lng'} for a city, using/filling the provided cache dict.

    Cache values: a dict with lat/lng, or None (negative cache) if not found.
    """
    if not city:
        return None
    key = _cache_key(city, country)
    if key in cache:
        return cache[key]

    global _last_call
    wait = 1.1 - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()

    try:
        # Structured query: Nominatim rejects free-form `q` combined with `country`,
        # so use the `city` + `countrycodes` form.
        resp = http.get(
            NOMINATIM_URL,
            params={
                "city": city,
                "countrycodes": (country or "nl"),
                "format": "json",
                "limit": "1",
            },
        )
        data = resp.json()
    except Exception:  # noqa: BLE001 - geocoding is best-effort
        data = []

    result = None
    if data:
        result = {"lat": float(data[0]["lat"]), "lng": float(data[0]["lon"])}
    cache[key] = result
    return result
