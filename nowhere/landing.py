"""Landing spot pool — picks a random biome coordinate with jitter."""

from __future__ import annotations

import json
import pathlib
import random

_DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
_POOL_PATH = _DATA_DIR / "pool.json"
_PATCH_PATH = _DATA_DIR / "places_patch.json"

_pool: list[dict] | None = None
_patch_jitter: dict[str, float] | None = None

# Destinations where water landing is intentional (not a bug).
_WATER_DESTINATIONS: frozenset[str] = frozenset({
    "大堡礁", "北大西洋", "死海", "里海",
})

# Biomes that should never be water landings.
_LAND_BIOMES: frozenset[str] = frozenset({
    "city", "mountain", "desert", "tundra", "rainforest", "forest",
    "volcano", "island",
})

# 8 cardinal + intercardinal directions for nudge search.
_NUDGE_DIRS: list[tuple[float, float]] = [
    (0.5, 0), (-0.5, 0), (0, 0.5), (0, -0.5),
    (0.35, 0.35), (0.35, -0.35), (-0.35, 0.35), (-0.35, -0.35),
]


def _load_pool() -> list[dict]:
    global _pool
    if _pool is None:
        with open(_POOL_PATH, encoding="utf-8") as f:
            _pool = json.load(f)
    return _pool


def _load_patch_jitter() -> dict[str, float]:
    """Load places_patch.json entries that have a jitter_deg field.

    Returns {place_name: jitter_deg} for regional features.
    """
    global _patch_jitter
    if _patch_jitter is None:
        _patch_jitter = {}
        if _PATCH_PATH.exists():
            try:
                data = json.loads(_PATCH_PATH.read_text(encoding="utf-8"))
                for name, info in data.items():
                    if isinstance(info, dict) and "jitter_deg" in info:
                        _patch_jitter[name] = float(info["jitter_deg"])
            except (json.JSONDecodeError, OSError):
                pass
    return _patch_jitter


def _is_water_destination(name_hint: str) -> bool:
    """Return True if this destination is intentionally water."""
    return name_hint in _WATER_DESTINATIONS


def _pool_surface_for(lat: float, lon: float) -> str | None:
    """Return the pool entry's surface for the nearest entry within 0.15 deg.

    Pool data is hand-verified and more reliable than the 1-degree grid for
    coastal/island locations.  Returns None if no matching entry found.
    """
    best_d = 0.25  # must be > 2*jitter (0.1° per axis = 0.2° Manhattan worst case)
    best_surface: str | None = None
    for entry in _load_pool():
        if "surface" not in entry:
            continue
        d = abs(entry["lat"] - lat) + abs(entry["lon"] - lon)
        if d < best_d:
            best_d = d
            best_surface = entry["surface"]
    return best_surface


def nudge_if_water(
    lat: float, lon: float, name_hint: str, biome: str = "",
) -> dict:
    """If (lat, lon) is on water and destination is not a water destination,
    search nearby for land.  Returns dict with lat, lon, and optionally
    "water_landing": true if no land found within search radius.

    Uses pool.json surface data (authoritative) when available,
    falls back to terrain grid for non-pool locations.
    As last resort, biome-based inference: city/mountain/desert/etc. on
    a coarse grid cell marked water is almost certainly a grid artifact.
    """
    from nowhere.terrain import surface as terrain_surface

    # Check pool data first (hand-verified, reliable for coastal cities)
    pool_surf = _pool_surface_for(lat, lon)
    if pool_surf is not None:
        is_water = pool_surf.startswith("water")
    else:
        is_water = terrain_surface(lat, lon).startswith("water")

    if not is_water:
        return {"lat": lat, "lon": lon}

    if _is_water_destination(name_hint):
        return {"lat": lat, "lon": lon}

    # Search for nearest land using terrain grid
    for step in (0.35, 0.5, 0.7):
        for dlat, dlon in _NUDGE_DIRS:
            scale = step / 0.5
            nlat = lat + dlat * scale
            nlon = lon + dlon * scale
            ns = terrain_surface(nlat, nlon)
            if not ns.startswith("water"):
                return {"lat": nlat, "lon": nlon}

    # Biome fallback: city/mountain/etc. on 1-degree water cell is a grid
    # artifact for coastal/island destinations.  Treat as land.
    if biome in _LAND_BIOMES:
        return {"lat": lat, "lon": lon}

    # No land found — mark as water landing
    return {"lat": lat, "lon": lon, "water_landing": True}


def random_spot(rng: random.Random) -> dict:
    """Pick a random landing spot from pool.json and add +/-0.1deg jitter.

    抖动收紧到 0.1°(约 11km): 池里的点是手核的真实海拔/地表,
    terrain 在 0.15° 半径内优先用池值,抖太远就吃不到真值了。
    Returns {"lat", "lon", "biome", "name_hint"}.
    """
    pool = _load_pool()
    spot = rng.choice(pool)
    return {
        "lat": spot["lat"] + rng.uniform(-0.1, 0.1),
        "lon": spot["lon"] + rng.uniform(-0.1, 0.1),
        "biome": spot["biome"],
        "name_hint": spot["name_hint"],
    }
