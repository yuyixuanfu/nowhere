"""Build high-res elevation tiles from AWS Terrarium (SRTM-derived) PNG tiles.

Why Terrarium instead of raw SRTM HGT:
  - one 256x256 PNG is ~60-80 KB vs a 1deg HGT at 26 MB raw / 6 MB gz
  - Terrarium keeps ocean bathymetry; SRTM HGT is void over water
  - decoding is just RGB -> metres, no big-endian int16 reshaping

Usage
-----
python tools/build_tiles_terrarium.py --zoom 11
python tools/build_tiles_terrarium.py --zoom 11 --only 特罗姆瑟
"""
from __future__ import annotations

import argparse
import io
import json
import math
import pathlib
import time
import urllib.request

import numpy as np
from PIL import Image

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DATA_DIR = _ROOT / "nowhere" / "data"
_TILES_DIR = _DATA_DIR / "tiles"
_POOL_PATH = _DATA_DIR / "pool.json"

HALF_EXTENT_DEG = 0.225      # ~25 km each way, matches build_tiles.py
STEP_DEG = 0.0005            # ~55 m output grid, matches build_tiles.py
BASE = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium"

# cover codes -- must match nowhere/terrain.py _SURFACE_MAP
WATER_OCEAN, WATER_FRESH, ROCK, SAND, SNOW, ICE, FOREST, GRASS, URBAN, BARE, WETLAND = range(11)


def deg2tile(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    r = math.radians(lat)
    y = (1.0 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2.0 * n
    return x, y


def tile2deg(x: float, y: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def fetch_tile(z: int, x: int, y: int, retries: int = 3) -> np.ndarray | None:
    url = f"{BASE}/{z}/{x}/{y}.png"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nowhere-tiles/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                img = Image.open(io.BytesIO(r.read()))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                a = np.asarray(img, dtype=np.float32)
            return a[:, :, 0] * 256.0 + a[:, :, 1] + a[:, :, 2] / 256.0 - 32768.0
        except Exception as exc:  # noqa: BLE001
            if attempt == retries - 1:
                print(f"    FAIL {z}/{x}/{y}: {exc}")
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def build_tile(center_lat: float, center_lon: float, z: int) -> dict | None:
    lat_min = center_lat - HALF_EXTENT_DEG
    lat_max = center_lat + HALF_EXTENT_DEG
    lon_min = center_lon - HALF_EXTENT_DEG
    lon_max = center_lon + HALF_EXTENT_DEG

    nlat = int(round((lat_max - lat_min) / STEP_DEG))
    nlon = int(round((lon_max - lon_min) / STEP_DEG))
    lats = np.linspace(lat_min, lat_max, nlat)
    lons = np.linspace(lon_min, lon_max, nlon)

    # Which source tiles cover the box?
    x0f, y1f = deg2tile(lat_min, lon_min, z)   # south-west
    x1f, y0f = deg2tile(lat_max, lon_max, z)   # north-east
    tx0, tx1 = int(math.floor(x0f)), int(math.floor(x1f))
    ty0, ty1 = int(math.floor(y0f)), int(math.floor(y1f))

    size = 256
    mosaic_w = (tx1 - tx0 + 1) * size
    mosaic_h = (ty1 - ty0 + 1) * size
    mosaic = np.full((mosaic_h, mosaic_w), np.nan, dtype=np.float32)

    got = 0
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            arr = fetch_tile(z, tx, ty)
            if arr is None:
                continue
            ox = (tx - tx0) * size
            oy = (ty - ty0) * size
            mosaic[oy:oy + size, ox:ox + size] = arr
            got += 1
    if got == 0:
        return None

    # Bilinear sample the mosaic at each output lat/lon.
    elev = np.zeros((nlat, nlon), dtype=np.float32)
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    fx = np.empty(lon_grid.shape, dtype=np.float64)
    fy = np.empty(lon_grid.shape, dtype=np.float64)
    # vectorised deg->global pixel
    n = 2 ** z
    fx_global = (lon_grid + 180.0) / 360.0 * n * size
    rad = np.radians(lat_grid)
    fy_global = (1.0 - np.log(np.tan(rad) + 1.0 / np.cos(rad)) / math.pi) / 2.0 * n * size
    fx = fx_global - tx0 * size
    fy = fy_global - ty0 * size

    x0i = np.clip(np.floor(fx).astype(int), 0, mosaic_w - 1)
    y0i = np.clip(np.floor(fy).astype(int), 0, mosaic_h - 1)
    x1i = np.clip(x0i + 1, 0, mosaic_w - 1)
    y1i = np.clip(y0i + 1, 0, mosaic_h - 1)
    ax = (fx - x0i).astype(np.float32)
    ay = (fy - y0i).astype(np.float32)

    v00 = mosaic[y0i, x0i]
    v01 = mosaic[y0i, x1i]
    v10 = mosaic[y1i, x0i]
    v11 = mosaic[y1i, x1i]
    blend = (v00 * (1 - ax) * (1 - ay) + v01 * ax * (1 - ay)
             + v10 * (1 - ax) * ay + v11 * ax * ay)
    elev = np.where(np.isnan(blend), 0.0, blend).astype(np.float32)

    # Surface classification.
    # Latitude-aware: a 2500 m peak in Hawaii is lava rock, not snow.
    surface = np.zeros(elev.shape, dtype=np.uint8)
    abslat = np.abs(lat_grid)
    land = elev >= -10

    surface[elev < -10] = WATER_OCEAN
    surface[land & (elev < 0)] = WATER_FRESH

    # Bare / rock / snow: the snow line falls with latitude.
    snow_line = np.where(abslat >= 60, 500.0,
                 np.where(abslat >= 45, 2200.0,
                  np.where(abslat >= 30, 4000.0, 5000.0)))
    bare_line = snow_line - 1200.0
    rock_line = snow_line - 600.0

    surface[land & (elev >= rock_line)] = ROCK
    surface[land & (elev >= bare_line) & (elev < rock_line)] = BARE
    surface[land & (elev >= snow_line)] = SNOW

    low = land & (elev >= 0) & (elev < bare_line)
    surface[low & (abslat > 55)] = GRASS
    surface[low & (abslat > 25) & (abslat <= 55)] = GRASS
    surface[low & (abslat <= 25)] = FOREST

    # Polar ice overrides low-lying cold land
    surface[land & (abslat > 66) & (elev < 500)] = ICE

    return {
        "elev": elev,
        "surface": surface,
        "lat_min": float(lat_min),
        "lat_max": float(lat_max),
        "lon_min": float(lon_min),
        "lon_max": float(lon_max),
        "shape": elev.shape,
    }


def tile_filename(lat: float, lon: float) -> str:
    return f"tile_{lat:+.3f}_{lon:+.3f}.npz".replace("+", "p").replace("-", "m")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zoom", type=int, default=11)
    ap.add_argument("--only", type=str, default=None, help="only build spots whose name_hint contains this")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    _TILES_DIR.mkdir(parents=True, exist_ok=True)
    pool = json.loads(_POOL_PATH.read_text(encoding="utf-8"))
    if args.only:
        pool = [s for s in pool if args.only in (s.get("name_hint") or "")]
    if args.limit:
        pool = pool[: args.limit]

    index: dict[str, dict] = {}
    idx_path = _TILES_DIR / "index.json"
    if idx_path.exists():
        try:
            index = json.loads(idx_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            index = {}

    total_bytes = 0
    t0 = time.time()
    for i, spot in enumerate(pool):
        lat, lon = float(spot["lat"]), float(spot["lon"])
        name = spot.get("name_hint") or f"spot_{i}"
        fname = tile_filename(lat, lon)
        out = _TILES_DIR / fname
        # Resume support: a previous run may have been interrupted.  Skip
        # spots whose tile already exists so a restart costs nothing.
        if out.exists() and out.stat().st_size > 1024:
            index[f"{lat:.4f},{lon:.4f}"] = {
                "file": fname,
                "lat_min": lat - HALF_EXTENT_DEG,
                "lat_max": lat + HALF_EXTENT_DEG,
                "lon_min": lon - HALF_EXTENT_DEG,
                "lon_max": lon + HALF_EXTENT_DEG,
            }
            print(f"[{i+1}/{len(pool)}] {name} ... SKIP (exists)")
            continue
        print(f"[{i+1}/{len(pool)}] {name} ({lat:.4f}, {lon:.4f}) ...", end=" ", flush=True)
        tile = build_tile(lat, lon, args.zoom)
        if tile is None:
            print("SKIP (no data)")
            continue
        np.savez_compressed(
            out,
            elev=tile["elev"],
            surface=tile["surface"],
            lat_min=np.float64(tile["lat_min"]),
            lat_max=np.float64(tile["lat_max"]),
            lon_min=np.float64(tile["lon_min"]),
            lon_max=np.float64(tile["lon_max"]),
        )
        size = out.stat().st_size
        total_bytes += size
        print(f"OK {size:,}B shape={tile['shape']} elev {tile['elev'].min():.0f}..{tile['elev'].max():.0f}")
        index[f"{lat:.4f},{lon:.4f}"] = {
            "file": fname,
            "lat_min": tile["lat_min"],
            "lat_max": tile["lat_max"],
            "lon_min": tile["lon_min"],
            "lon_max": tile["lon_max"],
        }
        if (i + 1) % 10 == 0:
            idx_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    idx_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    dt = time.time() - t0
    print(f"\nDone. {len(index)} tiles in {dt:.0f}s ({dt/max(len(pool),1):.1f}s each)")
    print(f"Total: {total_bytes:,} bytes ({total_bytes/1024/1024:.1f} MB)")


if __name__ == "__main__":
    main()
