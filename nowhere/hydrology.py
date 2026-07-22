"""Hydrology module -- find nearby water features via OSM Overpass API.

Returns literary descriptions of rivers, lakes, streams, waterfalls,
and reservoirs near a given coordinate.
"""

from __future__ import annotations

import math
import pathlib
import random
from typing import Any

from nowhere import providers

# ── Constants ───────────────────────────────────────────────────────
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_SCENE_DIR = pathlib.Path(__file__).resolve().parent / "data"
_SCENE_FILE = "water_features"

# Bearing labels (8 directions)
_BEARINGS: list[str] = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]

# Map OSM tag values to our type names
_WATERWAY_TO_TYPE: dict[str, str] = {
    "river": "river",
    "stream": "stream",
    "canal": "stream",
    "waterfall": "waterfall",
}
_WATER_TO_TYPE: dict[str, str] = {
    "river": "river",
    "lake": "lake",
    "reservoir": "reservoir",
    "pond": "lake",
    "stream": "stream",
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km."""
    rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
    rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    a = min(a, 1.0)  # clamp for floating-point safety on antipodal points
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bearing_label(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """Return compass bearing label (N/NE/E/SE/S/SW/W/NW) from point 1 to 2."""
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    bearing = (math.degrees(math.atan2(x, y)) + 360) % 360
    idx = round(bearing / 45) % 8
    return _BEARINGS[idx]


def _classify_element(tags: dict[str, str]) -> str | None:
    """Classify an OSM element into our water type, or None to skip."""
    # Waterways take priority
    ww = tags.get("waterway")
    if ww:
        return _WATERWAY_TO_TYPE.get(ww)
    # natural=water
    if tags.get("natural") == "water":
        water = tags.get("water", "")
        return _WATER_TO_TYPE.get(water, "lake")
    return None


def _element_center(el: dict[str, Any]) -> tuple[float, float] | None:
    """Extract (lat, lon) from an Overpass element."""
    if "lat" in el and "lon" in el:
        return el["lat"], el["lon"]
    center = el.get("center")
    if center and "lat" in center and "lon" in center:
        return center["lat"], center["lon"]
    return None


async def nearby_water(lat: float, lon: float, radius_km: float = 10) -> list[dict]:
    """Query OSM Overpass for water features within *radius_km*.

    Returns a list of dicts:
        {"name": str, "type": "river"|"lake"|"stream"|"waterfall"|"reservoir",
         "distance_km": float, "bearing": str, "detail": str}

    Empty list means no water nearby.
    """
    radius_m = int(radius_km * 1000)
    query = (
        f'[out:json][timeout:10];'
        f'('
        f'  node["natural"="water"](around:{radius_m},{lat},{lon});'
        f'  way["natural"="water"](around:{radius_m},{lat},{lon});'
        f'  node["waterway"="waterfall"](around:{radius_m},{lat},{lon});'
        f'  way["waterway"="river"]["name"](around:{radius_m},{lat},{lon});'
        f'  relation["waterway"="river"]["name"](around:{radius_m},{lat},{lon});'
        f');'
        f'out center tags;'
    )
    url = f"{_OVERPASS_URL}?data={query}"
    data = await providers.fetch_json(url, source="overpass", cache_ttl=3600, timeout=15.0)
    if data is None:
        return []

    elements = data.get("elements", [])
    results: list[dict] = []

    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name", "").strip()
        wtype = _classify_element(tags)
        if wtype is None:
            continue
        center = _element_center(el)
        if center is None:
            continue

        elat, elon = center
        dist = _haversine_km(lat, lon, elat, elon)
        bearing = _bearing_label(lat, lon, elat, elon)

        # Build a detail string from useful OSM tags
        detail_parts: list[str] = []
        if tags.get("width"):
            detail_parts.append(f"宽{tags['width']}米")
        if tags.get("depth"):
            detail_parts.append(f"深{tags['depth']}米")
        detail = ",".join(detail_parts)

        results.append({
            "name": name or "无名水域",
            "type": wtype,
            "distance_km": round(dist, 1),
            "bearing": bearing,
            "detail": detail,
        })

    # Sort by distance first, then deduplicate (closest wins)
    results.sort(key=lambda r: r["distance_km"])
    seen_names: set[str] = set()
    deduped: list[dict] = []
    for r in results:
        dedup_key = f"{r['name']}|{r['type']}"
        if r["name"] != "无名水域" and dedup_key in seen_names:
            continue
        seen_names.add(dedup_key)
        deduped.append(r)
    results = deduped
    return results


def describe_water(features: list[dict], rng: random.Random, biome: str = "") -> str:
    """Pick the most interesting water feature and render a literary description.

    Returns "" if no features.
    """
    if not features:
        return ""

    # Pick the most interesting: waterfall > river > lake > stream > reservoir
    priority = {"waterfall": 0, "river": 1, "lake": 2, "stream": 3, "reservoir": 4}
    ranked = sorted(features, key=lambda f: (priority.get(f["type"], 9), f["distance_km"]))
    feature = ranked[0]

    # Load scene file
    fp = _SCENE_DIR / f"scene_{_SCENE_FILE}.txt"
    if fp.exists():
        lines = [l.strip() for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()]
    else:
        lines = []

    # Biome filtering: exclude scenes inappropriate for the biome
    # scene_water_features.txt indices (0-based):
    # 0: stream, 1: lake, 2: river, 3: frozen river, 4: braided river,
    # 5: creek, 6: frozen lake, 7: waterfall, 8: river beach, 9: well
    if lines and biome:
        _LAKE_IDX = {1, 6}
        if biome in ("tundra", "desert", "coast"):
            filtered = [s for i, s in enumerate(lines) if i not in _LAKE_IDX]
            if filtered:
                lines = filtered

    if lines:
        # Pick a scene that roughly matches the water type
        text = rng.choice(lines)
    else:
        # Fallback: minimal prose
        text = f"{feature['bearing']}边有水。"

    return text
