"""Real aurora forecast from NOAA SWPC OVATION model.

OVATION gives an aurora power value 0-15 on a 1-degree global grid
(360 lon x 181 lat). We fetch the whole grid once, cache it, and look up
the cell nearest to the requested coordinate.

Why OVATION instead of the planetary K index:
  Kp is one number for the whole planet.  It says "there is a storm" but
  not "you can see it from here".  OVATION is a real model output that
  accounts for the auroral oval's shape, local magnetic time and the
  observer's longitude, so two places at 70N on the same night can get
  different answers -- which is what we want when the player is standing
  in Tromso versus standing in Fairbanks.

Offline behaviour: returns None on any failure.  Callers must fall back to
the latitude heuristic in ``sky.py``; a missing forecast must never crash a
landing.
"""
from __future__ import annotations

import logging
import math
from typing import Final

import httpx

logger = logging.getLogger(__name__)

_OVATION_URL: Final = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
_CACHE_TTL_SECONDS: Final = 30 * 60   # OVATION refreshes roughly every 5-15 min
_TIMEOUT_SECONDS: Final = 6.0

# value 0-15 -> the intensity scale describe.py already understands (1-5)
_INTENSITY_BANDS: Final[list[tuple[int, int]]] = [
    (1, 1),    # 1-2   faint arc on the horizon
    (3, 2),    # 3-4   clear but quiet
    (6, 3),    # 6-7   moving
    (10, 4),   # 10-11 bright, fast
    (15, 5),   # 15+   storm
]

_cache: dict | None = None
_cache_at: float = 0.0

# After a failed fetch, stay quiet for a while.  Without this an offline
# device would burn 6 s of timeout on every single landing and walk step.
_FAIL_BACKOFF_SECONDS: Final = 10 * 60
_fail_at: float = 0.0


def _band(value: int) -> int:
    for upper, intensity in _INTENSITY_BANDS:
        if value <= upper:
            return intensity
    return 5


def _shape_for(value: int, lat: float) -> str:
    """Pick a shape.  Strong power -> structured forms, weak -> diffuse."""
    if value >= 10:
        return "corona" if abs(lat) > 66 else "curtain"
    if value >= 6:
        return "curtain"
    if value >= 3:
        return "arc"
    return "diffuse"


def _color_for(value: int, lat: float) -> str:
    """Green is the common low-altitude emission; red/purple needs power."""
    if value >= 10:
        return "purple_red"
    if value >= 6:
        return "green_purple"
    return "green"


def _fetch_grid() -> dict | None:
    """Fetch and cache the OVATION grid.  Returns None on any failure."""
    global _cache, _cache_at, _fail_at
    import time

    now = time.monotonic()
    if _cache is not None and (now - _cache_at) < _CACHE_TTL_SECONDS:
        return _cache
    if _fail_at and (now - _fail_at) < _FAIL_BACKOFF_SECONDS:
        return None
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = client.get(_OVATION_URL, headers={"User-Agent": "nowhere-mcp/1.0"})
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - any failure means "no forecast"
        logger.debug("ovation fetch failed: %s", exc)
        _fail_at = time.monotonic()
        return None

    coords = payload.get("coordinates")
    if not isinstance(coords, list) or not coords:
        logger.debug("ovation payload missing coordinates")
        return None

    # Flatten to a dense [lat_index][lon_index] table.  Latitude runs -90..90,
    # longitude 0..359, both at 1 degree.
    grid: list[list[int]] = [[0] * 360 for _ in range(181)]
    for item in coords:
        try:
            lon_i, lat_i, value = int(item[0]), int(item[1]), int(item[2])
        except (TypeError, ValueError, IndexError):
            continue
        if not (-90 <= lat_i <= 90 and 0 <= lon_i <= 359):
            continue
        grid[lat_i + 90][lon_i] = value

    _cache = {
        "grid": grid,
        "observation_time": payload.get("Observation Time"),
        "forecast_time": payload.get("Forecast Time"),
    }
    _cache_at = now
    _fail_at = 0.0
    return _cache


def forecast(lat: float, lon: float) -> dict | None:
    """Return the aurora forecast at (*lat*, *lon*), or ``None``.

    Shape of the returned dict matches what ``describe.py`` consumes:
    ``{"color", "shape", "intensity"}`` plus provenance fields.

    Returns ``None`` when:
      - the coordinate is outside the auroral zone (|lat| < 55) so we do not
        invent aurora over the tropics
      - the forecast is unreachable, so the caller can use its fallback
      - the local aurora power is 0 (nothing to see tonight)
    """
    if abs(lat) < 55.0:
        return None

    data = _fetch_grid()
    if data is None:
        return None

    lat_i = max(-90, min(90, int(round(lat))))
    lon_i = int(math.floor(lon)) % 360
    value = data["grid"][lat_i + 90][lon_i]

    if value <= 0:
        return None

    return {
        "color": _color_for(value, lat),
        "shape": _shape_for(value, lat),
        "intensity": _band(value),
        "power": value,
        "source": "noaa_ovation",
        "observation_time": data["observation_time"],
    }


def available() -> bool:
    """True if a real OVATION grid can be used right now.

    Lets the caller tell "the forecast says nothing is happening" (fall
    through to silence) apart from "there is no forecast" (fall back to the
    latitude heuristic).
    """
    return _fetch_grid() is not None


def reset_cache() -> None:
    """Drop the cached grid (tests / forced refresh)."""
    global _cache, _cache_at, _fail_at
    _cache = None
    _cache_at = 0.0
    _fail_at = 0.0
