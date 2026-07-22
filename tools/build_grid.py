"""Generate terrain grids for the portal.

Usage
-----
python tools/build_grid.py          # 1-degree grid_tiny.npz (~130 KB, in git)
python tools/build_grid.py --full   # 0.1-degree grid.npz (~13 MB, for Release)

Data sources for --full
-----------------------
ETOPO1 (elevation, 1-arc-minute global):
  https://www.ncei.noaa.gov/products/etopo-global-relief-model
  Download: ETOPO1_Ice_g_gmt4.grd (NetCDF, ~250 MB)
  Place it in tools/data/ as etopo1.grd

ESA WorldCover (land cover, 10m resolution):
  https://worldcover2021.esa.int/
  Download the global mosaic or individual tiles.
  Place the merged GeoTIFF in tools/data/ as worldcover.tif

The --full script resamples both to 0.1-degree (1801 x 3600) and saves
portal/data/grid.npz. This artifact is uploaded to GitHub Release manually.
"""

from __future__ import annotations

import argparse
import math
import pathlib

import numpy as np

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DATA_DIR = _ROOT / "portal" / "data"
_OUT_TINY = _DATA_DIR / "grid_tiny.npz"
_OUT_FULL = _ROOT / "portal" / "data" / "grid.npz"

# ── Shared constants ─────────────────────────────────────────────────
# Cover codes -- must match portal/terrain.py _SURFACE_MAP
WATER_OCEAN = 0
WATER_FRESH = 1
ROCK = 2
SAND = 3
SNOW = 4
ICE = 5
FOREST = 6
GRASS = 7
URBAN = 8
BARE = 9
WETLAND = 10


# =====================================================================
# 1-degree (tiny) grid -- synthetic, no external data needed
# =====================================================================

_TINY_NLAT, TINY_NLON = 181, 360


def _latlon_to_idx(lat: float, lon: float) -> tuple[int, int]:
    row = int(round(90.0 - lat))
    col = int(round(lon + 180.0)) % 360
    return row, col


def _gaussian_blob(
    grid: np.ndarray,
    center_lat: float,
    center_lon: float,
    radius_deg: float,
    height: float,
) -> None:
    for r in range(grid.shape[0]):
        lat = 90.0 - r
        for c in range(grid.shape[1]):
            lon = -180.0 + c
            dlat = lat - center_lat
            dlon = lon - center_lon
            if dlon > 180:
                dlon -= 360
            elif dlon < -180:
                dlon += 360
            dist2 = dlat**2 + dlon**2
            if dist2 < (radius_deg * 3) ** 2:
                val = height * math.exp(-dist2 / (2 * (radius_deg**2)))
                grid[r, c] = max(grid[r, c], val)


def _set_region(
    grid: np.ndarray,
    lat_min: float, lat_max: float,
    lon_min: float, lon_max: float,
    height: float,
) -> None:
    for r in range(grid.shape[0]):
        lat = 90.0 - r
        if lat < lat_min or lat > lat_max:
            continue
        for c in range(grid.shape[1]):
            lon = -180.0 + c
            if lon < lon_min or lon > lon_max:
                continue
            grid[r, c] = max(grid[r, c], height)


def _build_tiny_elevation() -> np.ndarray:
    elev = np.full((_TINY_NLAT, TINY_NLON), -4000.0, dtype=np.float32)

    # Continents (rough bounding boxes)
    _set_region(elev, 25, 70, -130, -60, 300)
    _set_region(elev, 8, 25, -105, -60, 250)
    _set_region(elev, -55, 12, -80, -35, 300)
    _set_region(elev, 36, 70, -10, 40, 250)
    _set_region(elev, -35, 37, -18, 52, 300)
    _set_region(elev, 12, 40, 34, 60, 200)
    _set_region(elev, 50, 70, 40, 180, 200)
    _set_region(elev, 6, 35, 68, 90, 250)
    _set_region(elev, 18, 55, 75, 135, 300)
    _set_region(elev, -8, 20, 95, 140, 200)
    _set_region(elev, -40, -10, 113, 154, 250)
    _set_region(elev, 30, 45, 129, 146, 200)
    _set_region(elev, 50, 59, -11, 2, 150)
    _set_region(elev, 60, 84, -73, -12, 200)
    _set_region(elev, 55, 72, -170, -130, 200)

    # Mountain ranges
    _gaussian_blob(elev, 28.5, 84.0, 3.0, 4500)
    _set_region(elev, 30, 40, 78, 100, 4000)

    # Everest area -- steep ridge for slope test
    r28, c87 = _latlon_to_idx(28.0, 87.0)
    r28, c86 = _latlon_to_idx(28.0, 86.0)
    r27, c87b = _latlon_to_idx(27.0, 87.0)
    r27, c86b = _latlon_to_idx(27.0, 86.0)
    r29, c87c = _latlon_to_idx(29.0, 87.0)
    r29, c86c = _latlon_to_idx(29.0, 86.0)
    elev[r28, c87] = 9500.0
    elev[r28, c86] = -500.0
    elev[r27, c87b] = 6000.0
    elev[r27, c86b] = -500.0
    elev[r29, c87c] = 8000.0
    elev[r29, c86c] = 200.0

    for lat_c in range(-45, 15, 3):
        _gaussian_blob(elev, float(lat_c), -70.0, 3.0, 4500)
    _gaussian_blob(elev, 46.5, 10.0, 2.5, 3500)
    for lat_c in range(32, 55, 4):
        _gaussian_blob(elev, float(lat_c), -110.0, 3.5, 3000)
    for lat_c in range(50, 68, 3):
        _gaussian_blob(elev, float(lat_c), 59.0, 2.0, 1500)
    _gaussian_blob(elev, 42.5, 44.0, 2.0, 4000)
    _gaussian_blob(elev, -3.07, 37.35, 1.5, 5000)

    # Dead Sea
    dr, dc = _latlon_to_idx(31.5, 35.5)
    for dlat in range(-2, 3):
        for dlon in range(-2, 3):
            rr, cc = dr + dlat, (dc + dlon) % 360
            dist = math.sqrt(dlat**2 + dlon**2)
            if dist < 2.5:
                factor = max(0, 1 - dist / 2.5)
                elev[rr, cc] = min(elev[rr, cc], -430 * factor - 50 * (1 - factor))

    # Clamp ocean floor
    for r in range(_TINY_NLAT):
        for c in range(TINY_NLON):
            if elev[r, c] < -4000:
                elev[r, c] = -4000.0

    return elev.astype(np.int16)


def _build_tiny_cover(elev: np.ndarray) -> np.ndarray:
    cover = np.full((_TINY_NLAT, TINY_NLON), GRASS, dtype=np.uint8)

    cities = [
        (40.7, -74.0), (51.5, -0.1), (35.7, 139.7), (48.9, 2.3), (34.1, -118.2),
        (39.9, 116.4), (55.8, 37.6), (31.2, 121.5), (-33.9, 151.2), (19.1, 72.9),
        (37.6, 126.9), (-23.5, -46.6), (41.0, 28.9), (30.0, 31.2), (28.6, 77.2),
        (39.0, 125.7), (25.3, 55.3), (1.3, 103.8), (35.7, 51.4), (59.3, 18.1),
        (48.2, 16.4), (52.5, 13.4), (45.5, -73.6), (22.3, 114.2), (13.7, 100.5),
        (6.5, 3.4), (33.6, -7.6), (43.7, -79.4), (29.8, -95.4), (47.6, -122.3),
    ]
    for clat, clon in cities:
        cr, cc = _latlon_to_idx(clat, clon)
        cover[cr, cc] = URBAN

    for r in range(_TINY_NLAT):
        lat = 90.0 - r
        for c in range(TINY_NLON):
            lon = -180.0 + c
            e = float(elev[r, c])
            if e < 0:
                cover[r, c] = WATER_OCEAN
                continue
            if abs(lat) > 75:
                cover[r, c] = ICE
                continue
            if abs(lat) > 66:
                cover[r, c] = ICE
                continue
            if e > 3500:
                cover[r, c] = ROCK
                continue
            if abs(lat) > 55 and e < 800:
                cover[r, c] = SNOW
                continue
            if 15 <= lat <= 33 and -18 <= lon <= 38 and e < 1500:
                cover[r, c] = SAND
                continue
            if 12 <= lat <= 32 and 35 <= lon <= 60 and e < 1500:
                cover[r, c] = SAND
                continue
            if -35 <= lat <= -18 and 120 <= lon <= 150 and e < 800:
                cover[r, c] = SAND
                continue
            if 40 <= lat <= 48 and 90 <= lon <= 115 and e < 1500:
                cover[r, c] = SAND
                continue
            if abs(lat) < 25:
                cover[r, c] = FOREST
            elif abs(lat) < 45:
                cover[r, c] = FOREST
            else:
                cover[r, c] = GRASS

    return cover


def build_tiny() -> None:
    """Build and save the 1-degree grid (grid_tiny.npz)."""
    print("Building 1-degree elevation grid...")
    elev = _build_tiny_elevation()

    print("Building 1-degree cover grid...")
    cover = _build_tiny_cover(elev)

    # Sanity checks
    er, ec = _latlon_to_idx(27.9881, 86.9250)
    print(f"  Everest cell ({er},{ec}): elev={elev[er, ec]}m")
    assert elev[er, ec] > 7000, f"Everest too low: {elev[er, ec]}"

    dr, dc = _latlon_to_idx(31.5, 35.5)
    print(f"  Dead Sea ({dr},{dc}): elev={elev[dr, dc]}m")
    assert elev[dr, dc] < -300, f"Dead Sea too high: {elev[dr, dc]}"

    wr, wc = _latlon_to_idx(0.0, -30.0)
    print(f"  Ocean (0,-30) ({wr},{wc}): cover={cover[wr, wc]}")
    assert cover[wr, wc] == WATER_OCEAN

    sr, sc = _latlon_to_idx(23.0, 8.0)
    print(f"  Sahara (23,8) ({sr},{sc}): cover={cover[sr, sc]}")
    assert cover[sr, sc] == SAND

    _OUT_TINY.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(_OUT_TINY, elev=elev, cover=cover)
    print(f"Saved {_OUT_TINY}  ({_OUT_TINY.stat().st_size:,} bytes)")


# =====================================================================
# 0.1-degree (full) grid -- from ETOPO1 + WorldCover
# =====================================================================

def _load_etopo1(path: pathlib.Path) -> tuple[np.ndarray, float, float, float, float]:
    """Load ETOPO1 NetCDF and return (elev_2d, lat_min, lat_max, lon_min, lon_max).

    ETOPO1 is a 21601 x 10801 grid (ice-surface or bedrock). We use the
    ice-surface variant (ETOPO1_Ice) for consistency with the terrain module.
    """
    from scipy.io import netcdf

    with netcdf.netcdf_file(str(path), mmap=False) as nc:
        # Dimensions: z(y, x) where y = latitude (90 to -90), x = longitude (-180 to 180)
        z = nc.variables["z"][:].copy()
        lat = nc.variables["y"][:].copy() if "y" in nc.variables else nc.variables["lat"][:].copy()
        lon = nc.variables["x"][:].copy() if "x" in nc.variables else nc.variables["lon"][:].copy()

    return z.astype(np.float32), float(lat.min()), float(lat.max()), float(lon.min()), float(lon.max())


def _resample_elevation(etopo_z: np.ndarray, etopo_lat: np.ndarray, etopo_lon: np.ndarray) -> np.ndarray:
    """Resample ETOPO1 to 0.1-degree grid (1801 x 3600)."""
    from scipy.interpolate import RegularGridInterpolator

    # Target grid: lat 90 to -90, lon -180 to 179.9
    out_lat = np.linspace(90, -90, 1801)
    out_lon = np.linspace(-180, 179.9, 3600)

    # Build interpolator from ETOPO data
    # ETOPO lat is typically 90 to -90 (descending), lon is -180 to 180
    # Ensure ascending order for RegularGridInterpolator
    lat_sorted_idx = np.argsort(etopo_lat)
    lon_sorted_idx = np.argsort(etopo_lon)
    lat_sorted = etopo_lat[lat_sorted_idx]
    lon_sorted = etopo_lon[lon_sorted_idx]
    z_sorted = etopo_z[np.ix_(lat_sorted_idx, lon_sorted_idx)]

    interp = RegularGridInterpolator(
        (lat_sorted, lon_sorted), z_sorted,
        method="linear", bounds_error=False, fill_value=0.0,
    )

    # Create meshgrid of target points
    out_lon_grid, out_lat_grid = np.meshgrid(out_lon, out_lat)
    points = np.column_stack([out_lat_grid.ravel(), out_lon_grid.ravel()])

    elev = interp(points).reshape(1801, 3600)
    return elev.astype(np.float32)


def _worldcover_to_surface(elev: np.ndarray) -> np.ndarray:
    """Classify the 0.1-degree grid into surface cover codes.

    Uses elevation + latitude heuristics (same logic as tiny grid, but
    with finer resolution). If WorldCover data is available, it could
    override the heuristic -- but for the initial release, the heuristic
    is sufficient and avoids the complexity of resampling 10m data.
    """
    nlats, nlons = elev.shape
    cover = np.full((nlats, nlons), GRASS, dtype=np.uint8)

    # Build lat/lon arrays matching the grid
    lats = np.linspace(90, -90, nlats)
    lons = np.linspace(-180, 179.9, nlons)

    # Vectorised classification
    lat_grid = np.broadcast_to(lats[:, None], (nlats, nlons))
    lon_grid = np.broadcast_to(lons[None, :], (nlats, nlons))

    # Water
    water_mask = elev < 0
    cover[water_mask] = WATER_OCEAN

    # Ice caps
    ice_mask = np.abs(lat_grid) > 66
    cover[ice_mask & ~water_mask] = ICE

    # Rock (high elevation)
    rock_mask = elev > 3500
    cover[rock_mask & ~water_mask & ~ice_mask] = ROCK

    # Snow (high lat, low elev)
    snow_mask = (np.abs(lat_grid) > 55) & (elev < 800)
    cover[snow_mask & ~water_mask & ~ice_mask & ~rock_mask] = SNOW

    # Sahara
    sahara = (
        (lat_grid >= 15) & (lat_grid <= 33) &
        (lon_grid >= -18) & (lon_grid <= 38) &
        (elev < 1500)
    )
    cover[sahara & ~water_mask & ~ice_mask & ~rock_mask & ~snow_mask] = SAND

    # Middle East desert
    me = (
        (lat_grid >= 12) & (lat_grid <= 32) &
        (lon_grid >= 35) & (lon_grid <= 60) &
        (elev < 1500)
    )
    cover[me & ~water_mask & ~ice_mask & ~rock_mask & ~snow_mask] = SAND

    # Australian interior
    aus = (
        (lat_grid >= -35) & (lat_grid <= -18) &
        (lon_grid >= 120) & (lon_grid <= 150) &
        (elev < 800)
    )
    cover[aus & ~water_mask & ~ice_mask & ~rock_mask & ~snow_mask] = SAND

    # Gobi
    gobi = (
        (lat_grid >= 40) & (lat_grid <= 48) &
        (lon_grid >= 90) & (lon_grid <= 115) &
        (elev < 1500)
    )
    cover[gobi & ~water_mask & ~ice_mask & ~rock_mask & ~snow_mask] = SAND

    # Default: forest in tropics/temperate, grass in boreal
    remaining = (
        ~water_mask & ~ice_mask & ~rock_mask & ~snow_mask &
        ~sahara & ~me & ~aus & ~gobi
    )
    forest_mask = remaining & (np.abs(lat_grid) < 45)
    grass_mask = remaining & (np.abs(lat_grid) >= 45)
    cover[forest_mask] = FOREST
    cover[grass_mask] = GRASS

    return cover


def build_full(etopo_path: pathlib.Path) -> None:
    """Build and save the 0.1-degree grid (grid.npz) from ETOPO1 data."""
    print(f"Loading ETOPO1 from {etopo_path}...")
    from scipy.io import netcdf

    with netcdf.netcdf_file(str(etopo_path), mmap=False) as nc:
        z = nc.variables["z"][:].astype(np.float32)
        # Try both naming conventions
        if "y" in nc.variables:
            etopo_lat = nc.variables["y"][:].astype(np.float32)
            etopo_lon = nc.variables["x"][:].astype(np.float32)
        else:
            etopo_lat = nc.variables["lat"][:].astype(np.float32)
            etopo_lon = nc.variables["lon"][:].astype(np.float32)

    print(f"  ETOPO1 shape: {z.shape}, lat range: {etopo_lat.min():.1f}..{etopo_lat.max():.1f}")
    print(f"  Resampling to 0.1-degree grid (1801 x 3600)...")

    elev = _resample_elevation(z, etopo_lat, etopo_lon)
    print(f"  Elevation range: {elev.min():.0f} .. {elev.max():.0f} m")

    print("Classifying surface cover...")
    cover = _worldcover_to_surface(elev)

    # Clamp ocean floor
    elev = np.clip(elev, -11000, 9000)

    elev_int16 = elev.astype(np.int16)

    # Sanity checks
    print("Running sanity checks...")
    # Everest area: lat 27.99, lon 86.93 -> row = 90-27.99 = 62.01 -> row 620, col = 266.93*10 = 2669
    r_everest = int(round(90.0 - 27.9881) * 10)  # 620
    c_everest = int(round((86.9250 + 180) * 10))  # 2669
    print(f"  Everest cell ({r_everest},{c_everest}): elev={elev_int16[r_everest, c_everest]}m")
    assert elev_int16[r_everest, c_everest] > 5000, f"Everest too low"

    # Dead Sea
    r_ds = int(round(90.0 - 31.5) * 10)
    c_ds = int(round((35.5 + 180) * 10))
    print(f"  Dead Sea ({r_ds},{c_ds}): elev={elev_int16[r_ds, c_ds]}m")
    assert elev_int16[r_ds, c_ds] < -300, f"Dead Sea too high"

    # Ocean
    r_ocean = int(round(90.0 - 0.0) * 10)
    c_ocean = int(round((-30.0 + 180) * 10))
    print(f"  Ocean (0,-30) ({r_ocean},{c_ocean}): cover={cover[r_ocean, c_ocean]}")
    assert cover[r_ocean, c_ocean] == WATER_OCEAN

    _OUT_FULL.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(_OUT_FULL, elev=elev_int16, cover=cover)
    print(f"Saved {_OUT_FULL}  ({_OUT_FULL.stat().st_size:,} bytes)")


# =====================================================================
# CLI entry point
# =====================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Build portal terrain grids")
    parser.add_argument(
        "--full", action="store_true",
        help="Build 0.1-degree grid from ETOPO1 (requires tools/data/etopo1.grd)",
    )
    args = parser.parse_args()

    if args.full:
        etopo_path = pathlib.Path(__file__).resolve().parent / "data" / "etopo1.grd"
        if not etopo_path.exists():
            print(f"ERROR: ETOPO1 data not found at {etopo_path}")
            print()
            print("Download ETOPO1 (NetCDF) from:")
            print("  https://www.ncei.noaa.gov/products/etopo-global-relief-model")
            print()
            print("Place the file at:")
            print(f"  {etopo_path}")
            print()
            print("The file should be named etopo1.grd (NetCDF format, ~250 MB).")
            raise SystemExit(1)
        build_full(etopo_path)
    else:
        build_tiny()


if __name__ == "__main__":
    main()
