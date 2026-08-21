"""DEM (Digital Elevation Model) fallback from cities15000.txt column 16.

The grid_tiny.npz uses 300 as a fill value for ~18.7% of cells where no
real DEM data exists.  This module reads the DEM column from cities15000
and provides a nearest-city elevation lookup as a fallback between tile
data and the raw grid.
"""

from __future__ import annotations

from typing import Final

from nowhere import city_index

# Threshold: grid value within this range of 300 is considered a fill value
_FILL_THRESHOLD: Final = 310  # grid values 290-310 are suspect fill values


def lookup(lat: float, lon: float) -> float | None:
    """Find DEM elevation from the nearest city in cities15000.

    Returns elevation in metres, or None if no city is within 50 km.
    """
    result = city_index.find_nearest(lat, lon, n=1, max_km=50.0)
    if result is None:
        return None
    dem = result[0][4]
    return dem if dem > 0 else None


def is_fill_value(elev: float) -> bool:
    """Check if a grid elevation value is likely a fill/placeholder (300m)."""
    return abs(elev - 300) < (_FILL_THRESHOLD - 300)
