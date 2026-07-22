"""Global encounter pool -- literary scene fragments per region.

Loads ``data/encounters.txt`` (one encounter per line, each prefixed with
a ``[region]`` tag) and draws random encounters filtered by geographic region.
"""

from __future__ import annotations

import pathlib
import random

_DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
_ENCOUNTER_FILE = "encounters.txt"

# Tags that appear in the merged encounter file.
_KNOWN_TAGS = frozenset(
    {"polar", "africa", "asia", "americas", "europe", "art", "natural"}
)

# Biome keywords that qualify as "urban" (art encounters mixed in).
_URBAN_BIOMES = frozenset(
    {"city", "town", "village", "settlement", "urban", "suburb", "port"}
)

_POOL: dict[str, list[str]] | None = None


def _load() -> dict[str, list[str]]:
    """Load and partition encounters.txt into per-region pools (cached)."""
    global _POOL
    if _POOL is not None:
        return _POOL

    fp = _DATA_DIR / _ENCOUNTER_FILE
    pools: dict[str, list[str]] = {tag: [] for tag in _KNOWN_TAGS}

    if fp.exists():
        for line in fp.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#") or stripped == "---":
                continue
            # Expect format: [tag] text
            if stripped.startswith("[") and "]" in stripped:
                bracket_end = stripped.index("]")
                tag = stripped[1:bracket_end].lower().strip()
                text = stripped[bracket_end + 1 :].strip()
                # Skip stray section headers (e.g. "[europe] [Europe]")
                if text.startswith("[") and text.endswith("]"):
                    continue
                if tag in pools and text:
                    pools[tag].append(text)

    _POOL = pools
    return _POOL


def _region_for(biome: str, lat: float, lon: float) -> str:
    """Return the region tag for a given position.

    Priority (same geographic logic as before):
      1. polar   -- |lat| > 60
      2. africa  -- roughly -35..37 N, -20..55 E
      3. asia    -- roughly 0..55 N, 60..150 E
      4. americas -- roughly -55..70, -170..-30
      5. europe  -- roughly 35..72 N, -15..40 E
      6. natural -- default for land without strong region signal
    """
    if lat > 60 or lat < -60:
        return "polar"

    if -35 <= lat <= 37 and -20 <= lon <= 55:
        return "africa"

    if 0 <= lat <= 55 and 60 <= lon <= 150:
        return "asia"

    if -55 <= lat <= 70 and -170 <= lon <= -30:
        return "americas"

    # Oceania folds into americas pool
    if -50 <= lat <= 0 and 110 <= lon <= 180:
        return "americas"

    if 35 <= lat <= 72 and -15 <= lon <= 40:
        return "europe"

    return "natural"


def draw_encounter(
    biome: str, lat: float, lon: float, rng: random.Random
) -> str | None:
    """Return a random encounter line for the given position, or None.

    1. Determine geographic region from lat/lon.
    2. Build a candidate pool: region lines + optional art/natural lines.
    3. Filter out climate-inappropriate encounters.
    4. Return a random choice with the ``[tag]`` prefix stripped.
    """
    pools = _load()
    region = _region_for(biome, lat, lon)

    # Start with the geographic region pool.
    candidates: list[str] = list(pools.get(region, []))

    # Mix in "natural" encounters (wilderness flavour).
    candidates.extend(pools.get("natural", []))

    # Mix in "art" encounters for urban / human-settlement biomes.
    biome_lower = biome.lower()
    if any(kw in biome_lower for kw in _URBAN_BIOMES):
        candidates.extend(pools.get("art", []))

    # Filter out climate-inappropriate encounters.
    abs_lat = abs(lat)
    # Tropical/rice paddy scenes only in subtropical/tropical regions (lat < 35)
    if abs_lat >= 35:
        tropical_keywords = ["稻田", "水牛", "梯田", "芭蕉", "椰子", "棕榈", "热带"]
        candidates = [c for c in candidates if not any(kw in c for kw in tropical_keywords)]
    # Snow/ice scenes only in cold regions (lat > 40 or lat < -40)
    if abs_lat < 40:
        cold_keywords = ["雪崩", "冰川", "冻土", "极光", "冰裂缝"]
        candidates = [c for c in candidates if not any(kw in c for kw in cold_keywords)]

    if not candidates:
        return None
    return rng.choice(candidates)
