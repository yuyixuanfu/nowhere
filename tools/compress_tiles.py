"""Compress large SRTM elevation tiles for lightweight portal deployment.

Reads each srtm_*.npz tile (6000x6000 float32, ~100MB),
downsamples to ~1000x1000 int16, and overwrites in-place (~2MB).

Usage:
    cd "C:/Users/84989/Desktop/新建文件夹 (7)"
    PYTHONIOENCODING=utf-8 python tools/compress_tiles.py
"""

from __future__ import annotations

import io
import json
import pathlib
import sys

import numpy as np

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TILES_DIR = pathlib.Path(__file__).resolve().parent.parent / "nowhere" / "data" / "tiles"
TARGET_SIZE = 1000  # target dimension (~1000x1000)


def _downsample_block_mean(arr: np.ndarray, target_h: int, target_w: int) -> np.ndarray:
    """Downsample 2-D array by block-averaging (area interpolation)."""
    h, w = arr.shape
    # Crop to exact multiple of target size
    bh = h // target_h
    bw = w // target_w
    cropped = arr[: bh * target_h, : bw * target_w]
    # Reshape and average
    blocks = cropped.reshape(target_h, bh, target_w, bw)
    return blocks.mean(axis=(1, 3))


def _downsample_nearest(arr: np.ndarray, target_h: int, target_w: int) -> np.ndarray:
    """Downsample 2-D array by nearest-neighbour (for categorical data)."""
    h, w = arr.shape
    row_idx = (np.arange(target_h) * h / target_h).astype(int)
    col_idx = (np.arange(target_w) * w / target_w).astype(int)
    return arr[np.ix_(row_idx, col_idx)]


def compress_tile(path: pathlib.Path) -> None:
    """Compress one SRTM tile in-place."""
    data = np.load(path, allow_pickle=True)
    elev = data["elev"]
    surface = data["surface"]
    lat_min = float(data["lat_min"])
    lat_max = float(data["lat_max"])
    lon_min = float(data["lon_min"])
    lon_max = float(data["lon_max"])

    orig_h, orig_w = elev.shape
    size_mb = path.stat().st_size / 1024 / 1024
    print(f"  {path.name}: {orig_h}x{orig_w} float32, {size_mb:.1f} MB")

    # Determine target dimensions (preserve aspect ratio)
    scale = TARGET_SIZE / max(orig_h, orig_w)
    target_h = max(1, int(orig_h * scale))
    target_w = max(1, int(orig_w * scale))

    # Replace nodata with 0 before downsampling
    nodata_mask = elev == -32768
    elev_clean = elev.copy()
    elev_clean[nodata_mask] = 0

    # Downsample elevation with block averaging, then convert to int16
    elev_small = _downsample_block_mean(elev_clean.astype(np.float64), target_h, target_w)
    elev_int16 = np.clip(np.round(elev_small), -32768, 32767).astype(np.int16)

    # Downsample surface with nearest-neighbour (categorical)
    surf_small = _downsample_nearest(surface, target_h, target_w).astype(np.uint8)

    # Overwrite original file
    np.savez_compressed(
        path,
        elev=elev_int16,
        surface=surf_small,
        lat_min=lat_min,
        lat_max=lat_max,
        lon_min=lon_min,
        lon_max=lon_max,
    )

    new_mb = path.stat().st_size / 1024 / 1024
    print(f"    -> {target_h}x{target_w} int16, {new_mb:.2f} MB  "
          f"({size_mb / new_mb:.0f}x smaller)")


def update_index() -> None:
    """Regenerate index.json from all .npz files in tiles/."""
    index = {}
    for f in sorted(TILES_DIR.glob("*.npz")):
        try:
            data = np.load(f, allow_pickle=True)
            lat_min = float(data["lat_min"])
            lat_max = float(data["lat_max"])
            lon_min = float(data["lon_min"])
            lon_max = float(data["lon_max"])
            lat_c = (lat_min + lat_max) / 2
            lon_c = (lon_min + lon_max) / 2
            key = f"{lat_c:.4f},{lon_c:.4f}"
            index[key] = f.name
        except Exception as e:
            print(f"    Warning: {f.name}: {e}")

    idx_path = TILES_DIR / "index.json"
    idx_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"\n  Index updated: {len(index)} tiles")


def main() -> None:
    srtm_files = sorted(TILES_DIR.glob("srtm_*.npz"))
    if not srtm_files:
        print("No srtm_*.npz files found in", TILES_DIR)
        return

    print(f"Compressing {len(srtm_files)} SRTM tiles ...\n")
    for path in srtm_files:
        compress_tile(path)

    print("\nUpdating index.json ...")
    update_index()
    print("\nDone.")


if __name__ == "__main__":
    main()
