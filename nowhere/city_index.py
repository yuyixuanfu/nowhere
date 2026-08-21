"""Shared spatial index for cities15000.txt.

Loads the file once, buckets cities into a 1x1-degree grid, and provides
``find_nearest()`` for O(1) nearest-city lookups instead of O(N) full scans.
"""

from __future__ import annotations

import math
import pathlib
from collections import defaultdict
from typing import Final

_PACK_PATH: Final = pathlib.Path(__file__).resolve().parent / "data" / "packs" / "cities15000.txt"

# Each entry: (lat, lon, country_code, population, dem_m)
# dem_m may be 0.0 if the source field was empty or non-positive.
_CityEntry = tuple[float, float, str, int, float]

_grid: dict[str, list[_CityEntry]] | None = None


def _load() -> None:
    global _grid
    if _grid is not None:
        return
    _grid = defaultdict(list)
    if not _PACK_PATH.exists():
        return
    with open(_PACK_PATH, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 17:
                continue
            try:
                lat = float(parts[4])
                lon = float(parts[5])
                cc = parts[8]
                pop = int(parts[14] or 0)
                dem_str = parts[16].strip()
                dem = float(dem_str) if dem_str else 0.0
            except (ValueError, IndexError):
                continue
            key = f"{int(math.floor(lat))},{int(math.floor(lon))}"
            _grid[key].append((lat, lon, cc, pop, dem))


def find_nearest(
    lat: float,
    lon: float,
    *,
    n: int = 1,
    max_km: float = math.inf,
    min_population: int = 0,
) -> list[_CityEntry] | None:
    """Return the *n* nearest cities to *(lat, lon)*.

    Searches the 3x3 grid of 1-degree cells surrounding the target.
    Returns a list of city entries sorted by distance, or ``None`` if
    no city satisfies the constraints.

    Parameters
    ----------
    min_population : int
        If > 0, skip cities with population below this threshold.
    max_km : float
        Ignore cities farther than this distance (haversine km).
    """
    _load()
    assert _grid is not None

    lat_int = int(math.floor(lat))
    lon_int = int(math.floor(lon))

    best: list[tuple[float, _CityEntry]] = []

    for dlat in (-1, 0, 1):
        for dlon in (-1, 0, 1):
            key = f"{lat_int + dlat},{lon_int + dlon}"
            for entry in _grid.get(key, ()):
                if min_population and entry[3] < min_population:
                    continue
                d = _haversine_km(lat, lon, entry[0], entry[1])
                if d > max_km:
                    continue
                if len(best) < n:
                    best.append((d, entry))
                    best.sort()
                elif d < best[-1][0]:
                    best[-1] = (d, entry)
                    best.sort()

    return [e for _, e in best] if best else None


def country_of(lat: float, lon: float) -> str | None:
    """Return the country code of the nearest city, or ``None``."""
    result = find_nearest(lat, lon, n=1, max_km=math.inf)
    if result is None:
        return None
    return result[0][2]


# ── Haversine ─────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km."""
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    a = min(a, 1.0)
    return 2 * 6371.0 * math.asin(math.sqrt(a))
