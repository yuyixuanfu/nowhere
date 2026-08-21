"""DEM (Digital Elevation Model) fallback from cities15000.txt column 16.

The grid_tiny.npz uses 300 as a fill value for ~18.7% of cells where no
real DEM data exists.  This module reads the DEM column from cities15000
and provides a nearest-city elevation lookup as a fallback between tile
data and the raw grid.
"""

from __future__ import annotations

import math
import pathlib
from typing import Final

_PACK_PATH: Final = pathlib.Path(__file__).resolve().parent / "data" / "packs" / "cities15000.txt"

# Cache: list of (lat, lon, dem_m)
_cities_dem: list[tuple[float, float, float]] | None = None

# Threshold: grid value within this range of 300 is considered a fill value
_FILL_THRESHOLD: Final = 310  # grid values 290-310 are suspect fill values


def _load_cities_dem() -> list[tuple[float, float, float]]:
    """Load cities15000.txt and extract (lat, lon, dem) for cities with valid DEM."""
    global _cities_dem
    if _cities_dem is not None:
        return _cities_dem
    _cities_dem = []
    if not _PACK_PATH.exists():
        return _cities_dem
    with open(_PACK_PATH, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 17:
                continue
            try:
                lat = float(parts[4])
                lon = float(parts[5])
                dem_str = parts[16].strip()
                if not dem_str:
                    continue
                dem = float(dem_str)
                if dem > 0:
                    _cities_dem.append((lat, lon, dem))
            except (ValueError, IndexError):
                continue
    return _cities_dem


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km."""
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    a = min(a, 1.0)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def lookup(lat: float, lon: float) -> float | None:
    """Find DEM elevation from the nearest city in cities15000.

    Returns elevation in metres, or None if no city is within 50 km.
    """
    cities = _load_cities_dem()
    if not cities:
        return None

    best_dist = 50.0  # max distance to consider (km)
    best_dem: float | None = None
    for clat, clon, dem in cities:
        d = _haversine_km(lat, lon, clat, clon)
        if d < best_dist:
            best_dist = d
            best_dem = dem
    return best_dem


def is_fill_value(elev: float) -> bool:
    """Check if a grid elevation value is likely a fill/placeholder (300m)."""
    return abs(elev - 300) < (_FILL_THRESHOLD - 300)
