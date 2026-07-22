"""Convert SRTM DEM UTM .img.zip files to elevation tiles for nowhere.

Usage:
    python tools/convert_srtm.py D:/edge/utm_srtm_*.zip

Reads SRTM UTM tiles, reprojects to WGS84, extracts elevation data,
and saves as .npz tiles in nowhere/data/tiles/.
"""
import sys
import io
import zipfile
import pathlib
import json
import numpy as np

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TILES_DIR = pathlib.Path(__file__).resolve().parent.parent / "nowhere" / "data" / "tiles"


def convert_zip(zip_path: str) -> list[str]:
    """Convert one .img.zip to .npz tiles. Returns list of created tile filenames."""
    import rasterio
    from rasterio.warp import transform_bounds

    zip_path = pathlib.Path(zip_path)
    print(f"\n  Processing: {zip_path.name}")

    # Extract .img from zip
    with zipfile.ZipFile(zip_path, 'r') as zf:
        img_names = [n for n in zf.namelist() if n.endswith('.img')]
        if not img_names:
            print(f"    No .img file found in {zip_path.name}")
            return []
        img_name = img_names[0]
        print(f"    Extracting: {img_name}")
        img_data = zf.read(img_name)

    # Write to temp file for rasterio
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.img', delete=False) as tmp:
        tmp.write(img_data)
        tmp_path = tmp.name

    try:
        with rasterio.open(tmp_path) as src:
            print(f"    Shape: {src.shape}, CRS: {src.crs}")
            print(f"    Bounds: {src.bounds}")

            # Reproject to WGS84 if needed
            if src.crs and not src.crs.to_epsg() == 4326:
                from rasterio.warp import calculate_default_transform, reproject, Resampling
                dst_crs = 'EPSG:4326'
                transform, width, height = calculate_default_transform(
                    src.crs, dst_crs, src.width, src.height, *src.bounds
                )
                kwargs = src.meta.copy()
                kwargs.update({
                    'crs': dst_crs,
                    'transform': transform,
                    'width': width,
                    'height': height,
                })

                # Read and reproject
                data = src.read(1)
                dst_data = np.empty((height, width), dtype=np.float32)
                reproject(
                    source=data,
                    destination=dst_data,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.bilinear,
                )
                bounds = transform_bounds(src.crs, dst_crs, *src.bounds)
                elev = dst_data
            else:
                elev = src.read(1).astype(np.float32)
                bounds = src.bounds

            # Get lat/lon bounds
            west, south, east, north = bounds
            print(f"    WGS84 bounds: {south:.2f},{west:.2f} to {north:.2f},{east:.2f}")

            # Create surface classification from elevation
            lat_center = (south + north) / 2
            cover = np.zeros_like(elev, dtype=np.uint8)
            for i in range(elev.shape[0]):
                lat = north - i * (north - south) / elev.shape[0]
                for j in range(elev.shape[1]):
                    e = elev[i, j]
                    if e < -10:
                        cover[i, j] = 0  # water
                    elif abs(lat) > 66:
                        cover[i, j] = 7  # ice
                    elif abs(lat) > 55 and e < 800:
                        cover[i, j] = 3  # snow
                    elif e > 3500:
                        cover[i, j] = 2  # rock
                    elif e > 2500:
                        cover[i, j] = 5  # grass (alpine)
                    else:
                        cover[i, j] = 5  # grass/forest

            # Flip vertically so row 0 = lat_min (south), matching _tile_bilinear expectation
            elev = elev[::-1].copy()
            cover = cover[::-1].copy()

            # Save as single tile
            tile_name = f"srtm_{south:.1f}_{west:.1f}.npz"
            tile_path = TILES_DIR / tile_name
            TILES_DIR.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                tile_path,
                elev=elev,
                surface=cover,
                lat_min=south,
                lat_max=north,
                lon_min=west,
                lon_max=east,
            )
            size_kb = tile_path.stat().st_size / 1024
            print(f"    Saved: {tile_name} ({size_kb:.0f} KB, {elev.shape[0]}x{elev.shape[1]})")
            return [tile_name]

    finally:
        import os
        os.unlink(tmp_path)


def update_index():
    """Regenerate index.json from all .npz files in tiles/."""
    index = {}
    for f in sorted(TILES_DIR.glob("*.npz")):
        if f.name == "index.json":
            continue
        try:
            data = np.load(f)
            lat_min = float(data['lat_min'])
            lat_max = float(data['lat_max'])
            lon_min = float(data['lon_min'])
            lon_max = float(data['lon_max'])
            # Use center as key
            lat_c = (lat_min + lat_max) / 2
            lon_c = (lon_min + lon_max) / 2
            key = f"{lat_c:.4f},{lon_c:.4f}"
            index[key] = f.name
        except Exception as e:
            print(f"    Warning: {f.name}: {e}")

    idx_path = TILES_DIR / "index.json"
    idx_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"\n  Index updated: {len(index)} tiles")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tools/convert_srtm.py <file1.zip> [file2.zip] ...")
        sys.exit(1)

    all_tiles = []
    for zip_file in sys.argv[1:]:
        tiles = convert_zip(zip_file)
        all_tiles.extend(tiles)

    print(f"\n  Total tiles created: {len(all_tiles)}")
    update_index()
