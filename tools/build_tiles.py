"""Generate high-resolution elevation tiles for the Portal project.

Each tile covers a ~50km radius around a landing spot at ~100m resolution.
Tiles are saved as compressed .npz files in portal/data/tiles/.

Usage
-----
python tools/build_tiles.py              # synthetic (upsample grid_tiny)
python tools/build_tiles.py --mode real  # Open-Meteo API (slow, 100/batch)

By default, generates synthetic tiles from grid_tiny.npz using bicubic
interpolation. The --mode real flag fetches real elevation data from
Open-Meteo, but is rate-limited to 100 coordinates per request and will
take a very long time for all 64 spots.

The tile infrastructure works the same either way -- real data can be
swapped in later by re-running with --mode real or replacing individual
tile files.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import time
from typing import Final

import numpy as np

# ── Paths ──────────────────────────────────────────────────────────────

_ROOT: Final = pathlib.Path(__file__).resolve().parent.parent
_DATA_DIR: Final = _ROOT / "portal" / "data"
_POOL_PATH: Final = _DATA_DIR / "pool.json"
_TINY_PATH: Final = _DATA_DIR / "grid_tiny.npz"
_TILES_DIR: Final = _DATA_DIR / "tiles"

# ── Tile parameters ────────────────────────────────────────────────────

HALF_EXTENT_DEG: Final = 0.225  # ~25km in each direction
STEP_DEG: Final = 0.0005       # ~55m per pixel
BATCH_SIZE: Final = 100        # Open-Meteo limit
DELAY_BETWEEN_BATCHES: Final = 0.3  # seconds

# ── Surface classification ─────────────────────────────────────────────

# Must match portal/terrain.py _SURFACE_MAP indices
_SURFACE_RULES: Final[list[tuple[str, float, float | None]]] = [
    # (name, threshold_low, threshold_high)  -- first match wins
    ("water_ocean", -99999, -10.0),
    ("rock",        3500.0, None),
    ("snow",        2500.0, 3500.0),
    ("bare",        1500.0, 2500.0),
    ("grass",       0.0,    1500.0),
]


def classify_surface(elev: float, lat: float = 0.0) -> str:
    """Simple elevation-based surface classification."""
    if elev < -10:
        return "water_ocean"
    if elev < 0:
        return "water_fresh"
    if abs(lat) > 66:
        if elev < 500:
            return "ice"
        return "snow"
    if elev > 3500:
        return "rock"
    if elev > 2500:
        return "snow"
    if elev > 1500:
        return "bare"
    if abs(lat) > 55:
        return "grass"
    if abs(lat) < 25:
        return "forest"
    return "grass"


# ── Synthetic tile generation (from grid_tiny) ─────────────────────────

def _load_tiny_grid() -> tuple[np.ndarray, np.ndarray]:
    """Load the 1-degree grid_tiny.npz."""
    data = np.load(_TINY_PATH)
    return data["elev"], data["cover"]


def _bilinear_interpolate(
    grid: np.ndarray,
    lat_points: np.ndarray,
    lon_points: np.ndarray,
) -> np.ndarray:
    """Bilinear interpolation on the tiny grid (181x360, lat 90..-90, lon -180..180)."""
    nrows, ncols = grid.shape  # 181, 360

    # Convert lat/lon to fractional row/col
    rows = 90.0 - lat_points
    cols = (lon_points + 180.0) % 360.0

    r0 = np.floor(rows).astype(int)
    c0 = np.floor(cols).astype(int)
    r1 = r0 + 1
    c1 = c0 + 1

    # Clamp
    r0 = np.clip(r0, 0, nrows - 1)
    r1 = np.clip(r1, 0, nrows - 1)
    c0 = c0 % ncols
    c1 = c1 % ncols

    fr = rows - np.floor(rows)
    fc = cols - np.floor(cols)

    # Bilinear blend
    v00 = grid[r0, c0].astype(np.float32)
    v01 = grid[r0, c1].astype(np.float32)
    v10 = grid[r1, c0].astype(np.float32)
    v11 = grid[r1, c1].astype(np.float32)

    return (
        v00 * (1 - fr) * (1 - fc)
        + v01 * (1 - fr) * fc
        + v10 * fr * (1 - fc)
        + v11 * fr * fc
    )


def build_synthetic_tile(
    center_lat: float,
    center_lon: float,
    tiny_elev: np.ndarray,
) -> dict:
    """Build a tile by upsampling grid_tiny via bilinear interpolation."""
    lat_min = center_lat - HALF_EXTENT_DEG
    lat_max = center_lat + HALF_EXTENT_DEG
    lon_min = center_lon - HALF_EXTENT_DEG
    lon_max = center_lon + HALF_EXTENT_DEG

    nlat = int(round((lat_max - lat_min) / STEP_DEG))
    nlon = int(round((lon_max - lon_min) / STEP_DEG))

    lats = np.linspace(lat_min, lat_max, nlat)
    lons = np.linspace(lon_min, lon_max, nlon)

    # Create meshgrid
    lon_grid, lat_grid = np.meshgrid(lons, lats)

    # Flatten for interpolation
    elev = _bilinear_interpolate(
        tiny_elev, lat_grid.ravel(), lon_grid.ravel()
    ).reshape(nlat, nlon)

    # Classify surface
    surface_codes = np.zeros((nlat, nlon), dtype=np.uint8)
    surface_map = {
        "water_ocean": 0, "water_fresh": 1, "rock": 2, "sand": 3,
        "snow": 4, "ice": 5, "forest": 6, "grass": 7,
        "urban": 8, "bare": 9, "wetland": 10,
    }
    for i in range(nlat):
        for j in range(nlon):
            s = classify_surface(float(elev[i, j]), float(lats[i]))
            surface_codes[i, j] = surface_map.get(s, 7)

    return {
        "elev": elev.astype(np.float32),
        "surface": surface_codes,
        "lat_min": lat_min,
        "lat_max": lat_max,
        "lon_min": lon_min,
        "lon_max": lon_max,
        "shape": (nlat, nlon),
    }


# ── Real tile generation (Open-Meteo API) ──────────────────────────────

def _fetch_elevation_batch(lats: list[float], lons: list[float]) -> list[float]:
    """Fetch elevations from Open-Meteo API (max 100 per call)."""
    import requests

    lat_str = ",".join(f"{v:.6f}" for v in lats)
    lon_str = ",".join(f"{v:.6f}" for v in lons)

    r = requests.get(
        "https://api.open-meteo.com/v1/elevation",
        params={"latitude": lat_str, "longitude": lon_str},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["elevation"]


def build_real_tile(
    center_lat: float,
    center_lon: float,
) -> dict:
    """Build a tile using real elevation data from Open-Meteo."""
    import requests  # noqa: F811

    lat_min = center_lat - HALF_EXTENT_DEG
    lat_max = center_lat + HALF_EXTENT_DEG
    lon_min = center_lon - HALF_EXTENT_DEG
    lon_max = center_lon + HALF_EXTENT_DEG

    nlat = int(round((lat_max - lat_min) / STEP_DEG))
    nlon = int(round((lon_max - lon_min) / STEP_DEG))

    lats = np.linspace(lat_min, lat_max, nlat)
    lons = np.linspace(lon_min, lon_max, nlon)

    # Flatten all coordinates
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    all_lats = lat_grid.ravel().tolist()
    all_lons = lon_grid.ravel().tolist()
    total = len(all_lats)

    elev_flat = np.zeros(total, dtype=np.float32)
    idx = 0
    batch_num = 0

    while idx < total:
        batch_end = min(idx + BATCH_SIZE, total)
        batch_lats = all_lats[idx:batch_end]
        batch_lons = all_lons[idx:batch_end]

        retries = 0
        while retries < 3:
            try:
                result = _fetch_elevation_batch(batch_lats, batch_lons)
                elev_flat[idx:batch_end] = result
                break
            except Exception as e:
                retries += 1
                if retries >= 3:
                    print(f"    FAILED batch {batch_num}: {e}")
                    # Fall back to 0
                    elev_flat[idx:batch_end] = 0
                else:
                    time.sleep(2)

        idx = batch_end
        batch_num += 1

        if batch_num % 100 == 0:
            pct = idx / total * 100
            print(f"    {pct:.0f}% ({idx}/{total} points)")
            time.sleep(DELAY_BETWEEN_BATCHES)
        elif batch_num % 10 == 0:
            time.sleep(DELAY_BETWEEN_BATCHES)

    elev = elev_flat.reshape(nlat, nlon)

    # Classify surface
    surface_codes = np.zeros((nlat, nlon), dtype=np.uint8)
    surface_map = {
        "water_ocean": 0, "water_fresh": 1, "rock": 2, "sand": 3,
        "snow": 4, "ice": 5, "forest": 6, "grass": 7,
        "urban": 8, "bare": 9, "wetland": 10,
    }
    for i in range(nlat):
        for j in range(nlon):
            s = classify_surface(float(elev[i, j]), float(lats[i]))
            surface_codes[i, j] = surface_map.get(s, 7)

    return {
        "elev": elev,
        "surface": surface_codes,
        "lat_min": lat_min,
        "lat_max": lat_max,
        "lon_min": lon_min,
        "lon_max": lon_max,
        "shape": (nlat, nlon),
    }


# ── Main ───────────────────────────────────────────────────────────────

def tile_filename(lat: float, lon: float) -> str:
    """Generate a consistent tile filename from coordinates."""
    return f"{lat:.4f}_{lon:.4f}.npz"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build portal elevation tiles")
    parser.add_argument(
        "--mode",
        choices=["synthetic", "real"],
        default="synthetic",
        help="synthetic: upsample grid_tiny (fast); real: Open-Meteo API (slow)",
    )
    parser.add_argument(
        "--spots",
        type=int,
        default=0,
        help="Limit to first N spots (0 = all)",
    )
    args = parser.parse_args()

    # Load pool
    pool = json.loads(_POOL_PATH.read_text(encoding="utf-8"))
    if args.spots > 0:
        pool = pool[: args.spots]
    print(f"Loaded {len(pool)} landing spots from pool.json")

    # Ensure output dir
    _TILES_DIR.mkdir(parents=True, exist_ok=True)

    # Load tiny grid for synthetic mode
    tiny_elev: np.ndarray | None = None
    if args.mode == "synthetic":
        tiny_elev, _ = _load_tiny_grid()
        print(f"Loaded grid_tiny.npz: shape={tiny_elev.shape}")

    index: dict[str, str] = {}
    total_bytes = 0
    start_time = time.time()

    for i, spot in enumerate(pool):
        lat = spot["lat"]
        lon = spot["lon"]
        name = spot.get("name_hint", f"spot_{i}")
        fname = tile_filename(lat, lon)
        out_path = _TILES_DIR / fname

        print(f"[{i+1}/{len(pool)}] {name} ({lat:.4f}, {lon:.4f}) ...", end=" ", flush=True)

        if args.mode == "synthetic":
            tile = build_synthetic_tile(lat, lon, tiny_elev)  # type: ignore
        else:
            tile = build_real_tile(lat, lon)

        # Save
        np.savez_compressed(
            out_path,
            elev=tile["elev"],
            surface=tile["surface"],
            lat_min=np.float64(tile["lat_min"]),
            lat_max=np.float64(tile["lat_max"]),
            lon_min=np.float64(tile["lon_min"]),
            lon_max=np.float64(tile["lon_max"]),
        )

        size = out_path.stat().st_size
        total_bytes += size
        print(f"OK  {size:,} bytes  shape={tile['shape']}")

        # Index key: "lat,lon" -- include bounds for fast lookup without loading tiles
        key = f"{lat:.4f},{lon:.4f}"
        index[key] = {
            "file": fname,
            "lat_min": tile["lat_min"],
            "lat_max": tile["lat_max"],
            "lon_min": tile["lon_min"],
            "lon_max": tile["lon_max"],
        }

    # Save index
    index_path = _TILES_DIR / "index.json"
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    elapsed = time.time() - start_time
    print()
    print(f"Done. {len(index)} tiles generated in {elapsed:.1f}s")
    print(f"Total size: {total_bytes:,} bytes ({total_bytes/1024/1024:.1f} MB)")
    print(f"Index: {index_path}")


if __name__ == "__main__":
    main()
