"""Synthetic terrain model for testing walk physics without real grid data.

See B12: grid_tiny.npz coastlines are blurred — near-shore water is misclassified
as land.  This module provides a clean in-memory terrain so logic tests can verify
water entry, cliff blocking, slope effects, and semantic direction selection with
controlled, repeatable terrain.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable
from unittest.mock import patch

import nowhere.terrain as _tmod

_EARTH_RADIUS_KM = 6371.0


@dataclass
class Tile:
    """A rectangular terrain tile with uniform elevation and surface."""

    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    elevation: float = 0.0
    surface: str = "grass"

    def contains(self, lat: float, lon: float) -> bool:
        return (
            self.lat_min <= lat <= self.lat_max
            and self.lon_min <= lon <= self.lon_max
        )


class World:
    """A synthetic world made of stacked terrain tiles.

    Tiles are checked in LIFO order — the last-added tile that contains the
    point wins.  Points outside all tiles fall back to *default_elevation*
    and *default_surface*.
    """

    def __init__(
        self,
        default_elevation: float = 0.0,
        default_surface: str = "grass",
    ):
        self.tiles: list[Tile] = []
        self.default_elevation = default_elevation
        self.default_surface = default_surface

    # -- builder API -------------------------------------------------------

    def tile(
        self,
        lat_min: float,
        lat_max: float,
        lon_min: float,
        lon_max: float,
        *,
        elev: float = 0.0,
        surface: str = "grass",
    ) -> World:
        """Add a rectangular tile (last-added wins on overlap)."""
        self.tiles.append(Tile(lat_min, lat_max, lon_min, lon_max, elev, surface))
        return self

    def coast(
        self,
        shore_lat: float,
        *,
        land_north: bool = True,
        land_elev: float = 20.0,
        land_surface: str = "grass",
        water_surface: str = "water_ocean",
    ) -> World:
        """Add two half-world tiles split at *shore_lat* to model a coastline."""
        if land_north:
            # land above shore_lat, water below
            self.tile(-90, shore_lat, -180, 180,
                      elev=0, surface=water_surface)
            self.tile(shore_lat, 90, -180, 180,
                      elev=land_elev, surface=land_surface)
        else:
            self.tile(-90, shore_lat, -180, 180,
                      elev=land_elev, surface=land_surface)
            self.tile(shore_lat, 90, -180, 180,
                      elev=0, surface=water_surface)
        return self

    def ramp(
        self,
        lat_start: float,
        lat_end: float,
        lon_min: float,
        lon_max: float,
        *,
        elev_start: float = 0.0,
        elev_end: float = 1000.0,
        surface: str = "grass",
    ) -> World:
        """Add a tile with linearly-interpolated elevation along latitude."""
        self.tiles.append(
            RampTile(lat_start, lat_end, lon_min, lon_max,
                     elev_start, elev_end, surface)
        )
        return self

    # -- terrain queries ---------------------------------------------------

    def _find(self, lat: float, lon: float) -> Tile | None:
        for t in reversed(self.tiles):
            if t.contains(lat, lon):
                return t
        return None

    def elevation(self, lat: float, lon: float) -> float:
        t = self._find(lat, lon)
        if t is None:
            return self.default_elevation
        if isinstance(t, RampTile):
            return t.elevation_at(lat)
        return t.elevation

    def surface(self, lat: float, lon: float) -> str:
        t = self._find(lat, lon)
        if t is None:
            return self.default_surface
        return t.surface

    def is_water(self, lat: float, lon: float) -> bool:
        return self.surface(lat, lon).startswith("water")

    def slope_between(
        self, a: tuple[float, float], b: tuple[float, float]
    ) -> tuple[float, float]:
        lat1, lon1 = a
        lat2, lon2 = b
        dist_km = self._haversine(lat1, lon1, lat2, lon2)
        e1 = self.elevation(lat1, lon1)
        e2 = self.elevation(lat2, lon2)
        diff_m = e2 - e1
        dist_m = dist_km * 1000.0
        if dist_m == 0:
            return (0.0, 0.0)
        slope_rad = math.atan2(abs(diff_m), dist_m)
        return (math.degrees(slope_rad), dist_km)

    def _haversine(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        lat1, lon1, lat2, lon2 = map(
            math.radians, (lat1, lon1, lat2, lon2)
        )
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))

    # -- patching ----------------------------------------------------------

    @contextmanager
    def patch(self):
        """Context manager: monkeypatch nowhere.terrain with this world."""
        w = self  # capture for closures

        def _elev(lat, lon):
            return w.elevation(lat, lon)

        def _surf(lat, lon):
            return w.surface(lat, lon)

        def _isw(lat, lon):
            return w.is_water(lat, lon)

        def _slope(a, b):
            return w.slope_between(a, b)

        with patch.object(_tmod, "elevation", side_effect=_elev) as pe, \
             patch.object(_tmod, "surface", side_effect=_surf) as ps, \
             patch.object(_tmod, "is_water", side_effect=_isw) as pw, \
             patch.object(_tmod, "slope_between", side_effect=_slope) as psl:
            yield {
                "elevation": pe,
                "surface": ps,
                "is_water": pw,
                "slope_between": psl,
            }


class RampTile(Tile):
    """A tile whose elevation varies linearly with latitude."""

    def __init__(
        self,
        lat_min: float,
        lat_max: float,
        lon_min: float,
        lon_max: float,
        elev_start: float,
        elev_end: float,
        surface: str = "grass",
    ):
        super().__init__(lat_min, lat_max, lon_min, lon_max,
                         elevation=0.0, surface=surface)
        self.elev_start = elev_start
        self.elev_end = elev_end

    def elevation_at(self, lat: float) -> float:
        frac = (lat - self.lat_min) / (self.lat_max - self.lat_min)
        frac = max(0.0, min(1.0, frac))
        return self.elev_start + frac * (self.elev_end - self.elev_start)
