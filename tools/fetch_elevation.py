"""Fetch elevation data from Open-Meteo API for nowhere grid/tile building.

Usage:
    python tools/fetch_elevation.py --mode grid    # Build 0.1° global grid
    python tools/fetch_elevation.py --mode tiles   # Build tiles for pool spots
    python tools/fetch_elevation.py --mode test     # Quick test with 100 points
"""
import argparse
import json
import pathlib
import time
import urllib.request
import urllib.parse
import sys
import io

# Force UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np

API = "https://api.open-meteo.com/v1/elevation"
BATCH = 50  # max coords per request
DELAY = 1.5  # seconds between requests (avoid 429)
MAX_RETRIES = 3
DATA = pathlib.Path(__file__).resolve().parent.parent / "nowhere" / "data"


def fetch_elevations(coords: list[tuple[float, float]]) -> list[float]:
    """Fetch elevations for a list of (lat, lon) pairs. Returns list of floats."""
    results = [0.0] * len(coords)
    for i in range(0, len(coords), BATCH):
        batch = coords[i:i + BATCH]
        lats = ",".join(f"{lat:.4f}" for lat, _ in batch)
        lons = ",".join(f"{lon:.4f}" for _, lon in batch)
        url = f"{API}?latitude={lats}&longitude={lons}"
        for attempt in range(MAX_RETRIES):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "nowhere-mcp/0.1"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                    elevs = data.get("elevation", [])
                    for j, e in enumerate(elevs):
                        results[i + j] = float(e) if e is not None else 0.0
                    break
            except Exception as e:
                if "429" in str(e) and attempt < MAX_RETRIES - 1:
                    wait = DELAY * (attempt + 2)
                    print(f"  Batch {i//BATCH} rate limited, waiting {wait:.0f}s...")
                    time.sleep(wait)
                else:
                    print(f"  Batch {i//BATCH} failed: {e}")
                    break
        if i + BATCH < len(coords):
            time.sleep(DELAY)
    return results


def build_grid_01deg():
    """Build 0.1° global elevation grid (1801×3601 points)."""
    print("Building 0.1° global grid...")
    lats = np.arange(90, -90.1, -0.1)  # 1801 points
    lons = np.arange(-180, 180.1, 0.1)  # 3601 points
    print(f"  Grid size: {len(lats)}×{len(lons)} = {len(lats)*len(lons)} points")

    # Build coordinate list
    coords = []
    for lat in lats:
        for lon in lons:
            coords.append((lat, lon))

    print(f"  Fetching {len(coords)} elevations in batches of {BATCH}...")
    elevs = fetch_elevations(coords)

    # Reshape to grid
    elev = np.array(elevs, dtype=np.float32).reshape(len(lats), len(lons))

    # Generate cover based on elevation + latitude
    cover = np.zeros_like(elev, dtype=np.uint8)
    for i, lat in enumerate(lats):
        for j, lon in enumerate(lons):
            e = elev[i, j]
            if e < -10:
                cover[i, j] = 0  # water_ocean
            elif abs(lat) > 66:
                cover[i, j] = 7  # ice
            elif abs(lat) > 55 and e < 800:
                cover[i, j] = 3  # snow
            elif e > 3500:
                cover[i, j] = 2  # rock
            elif (15 < abs(lat) < 35 and e < 1500 and
                  ((-5 < lon < 60) or (35 < lat < 50 and 25 < lon < 65))):
                cover[i, j] = 1  # sand (Sahara/Middle East/Australia)
            else:
                cover[i, j] = 5  # grass/forest

    out = DATA / "grid_01deg.npz"
    np.savez_compressed(out, elev=elev.astype(np.int16), cover=cover,
                        lats=lats.astype(np.float32), lons=lons.astype(np.float32))
    print(f"  Saved to {out} ({out.stat().st_size / 1024 / 1024:.1f} MB)")


def build_tiles():
    """Build elevation tiles for pool.json spots."""
    pool_path = DATA / "pool.json"
    pool = json.loads(pool_path.read_text(encoding="utf-8"))

    tiles_dir = DATA / "tiles"
    tiles_dir.mkdir(exist_ok=True)

    index = {}
    for i, spot in enumerate(pool):
        lat, lon = spot["lat"], spot["lon"]
        name = spot.get("name_hint", f"spot_{i}")
        print(f"  [{i+1}/{len(pool)}] {name} ({lat:.2f}, {lon:.2f})...")

        # 50km radius ≈ 0.45°
        half = 0.225
        res = 0.002  # ~200m resolution
        lats = np.arange(lat + half, lat - half - res, -res)
        lons = np.arange(lon - half, lon + half + res, res)

        coords = []
        for la in lats:
            for lo in lons:
                coords.append((la, lo))

        elevs = fetch_elevations(coords)
        elev = np.array(elevs, dtype=np.float32).reshape(len(lats), len(lons))

        tile_name = f"{lat:.2f}_{lon:.2f}".replace("-", "n").replace(".", "d") + ".npz"
        tile_path = tiles_dir / tile_name
        np.savez_compressed(tile_path, elev=elev,
                            lat_min=lat - half, lat_max=lat + half,
                            lon_min=lon - half, lon_max=lon + half,
                            name_hint=name)
        index[f"{lat:.4f},{lon:.4f}"] = tile_name
        print(f"    → {tile_name} ({tile_path.stat().st_size / 1024:.0f} KB)")

    # Save index
    idx_path = tiles_dir / "index.json"
    idx_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nIndex saved: {idx_path}")
    print(f"Total tiles: {len(index)}")


def build_test():
    """Quick test: fetch 100 points around Chongqing."""
    print("Test: fetching 100 points around Chongqing...")
    coords = [(29.5 + i * 0.01, 106.5 + j * 0.01) for i in range(10) for j in range(10)]
    elevs = fetch_elevations(coords)
    elev = np.array(elevs).reshape(10, 10)
    print(f"  Elevation range: {elev.min():.0f}m - {elev.max():.0f}m")
    print(f"  Grid:\n{elev}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["grid", "tiles", "test"], default="test")
    args = parser.parse_args()

    if args.mode == "grid":
        build_grid_01deg()
    elif args.mode == "tiles":
        build_tiles()
    else:
        build_test()
