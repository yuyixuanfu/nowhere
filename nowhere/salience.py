"""Salience ranking — pick the top-3 things the body should report.

Score = 0.5*delta + 0.3*novelty + 0.2*(1-body_distance).

Card 53: gravity dimension — heavy places warp the salience field.
  - 重地 5km 内: humanities 置顶(×2.5), 轻浮内容降级(×0.3)
  - 不是删除轻浮内容,是让它们在重力场里变轻

Card 69: Situation filtering — candidates must pass situation.permits()
before entering the ranking pool.  This prevents context-mixing where
content from one biome/season/place bleeds into another.

Only the top 3 survive; the rest stay silent in the data attachment.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

# ── Card 69: Situation — runtime context for content filtering ──────────

_LAT_BANDS_MAP = [
    (66, 90, "polar"),
    (35, 66, "north_temperate"),
    (-35, 35, "tropics"),
    (-90, -35, "south_temperate"),
]

_CLIMATE_ZONES = [
    (60, 90, "寒带"),
    (40, 60, "温带"),
    (23.5, 40, "暖温带"),
    (0, 23.5, "热带"),
]

_REGION_MAP = [
    (43, 50, 5, 18, "alpine"),
    (35, 70, -15, 40, "europe"),
    (45, 70, 20, 180, "russia"),
    (20, 55, 73, 145, "east_asia"),
    (-10, 25, 90, 155, "southeast_asia"),
    (5, 35, 60, 100, "south_asia"),
    (10, 45, 25, 65, "middle_east"),
    (-35, 37, -20, 55, "africa"),
    (10, 70, -170, -50, "north_america"),
    (-55, 15, -85, -35, "south_america"),
    (-50, 0, 110, 180, "oceania"),
    (66, 90, -180, 180, "arctic"),
]


def _get_lat_band_for_situation(lat: float) -> str:
    """Map latitude to card lat_band category."""
    abs_lat = abs(lat)
    if abs_lat > 66:
        return "polar"
    if abs_lat < 23.5:
        return "tropics"
    if lat > 0:
        return "north_temperate"
    return "south_temperate"


def _get_climate_zone_for_situation(lat: float, elev: float = 0) -> str:
    """Map latitude + elevation to climate zone."""
    if elev >= 3000:
        return "寒带"
    abs_lat = abs(lat)
    for lo, hi, zone in _CLIMATE_ZONES:
        if lo <= abs_lat < hi:
            return zone
    return "热带" if abs_lat < 23.5 else "寒带"


def _get_culture_region(lat: float, lon: float) -> str:
    """Map coordinates to cultural region for scene filtering."""
    for lat_min, lat_max, lon_min, lon_max, region in _REGION_MAP:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return region
    return "any"


def _infer_water_type(water_features: list[dict] | None) -> str | None:
    """Infer dominant water body type from water features list."""
    if not water_features:
        return None
    for f in water_features:
        ftype = f.get("type", "")
        fname = f.get("name", "")
        if "ocean" in ftype or fname.endswith("海"):
            return "ocean"
    for f in water_features:
        ftype = f.get("type", "")
        fname = f.get("name", "")
        if any(k in ftype for k in ("river", "溪", "江", "河")) or any(fname.endswith(k) for k in ("江", "河", "溪")):
            return "river"
    for f in water_features:
        ftype = f.get("type", "")
        fname = f.get("name", "")
        if "lake" in ftype or fname.endswith("湖"):
            return "lake"
    return None


@dataclass
class Situation:
    """Unified situation context for runtime content filtering.

    Built at open_door and walk time.  All content sources (weather, terrain,
    localcolor, seasonal, festival, radio, art, life, water) are filtered
    through this before entering the salience pool.
    """
    lat: float = 0.0
    lon: float = 0.0
    place: str = ""
    cc: str | None = None
    season: str = ""
    biome: str | None = None
    elev: float = 0.0
    climate_zone: str = ""
    culture_region: str = "any"
    water_type: str | None = None

    def permits(self, candidate: dict) -> bool:
        """Check if a candidate is allowed by the current situation.

        Returns False to reject (hard filter, not downrank).
        Each kind checks its relevant metadata fields against the situation.
        Missing payload metadata = universal card = passes.
        """
        kind = candidate.get("kind", "")
        payload = candidate.get("payload")

        # Weather, terrain, sky, blocked: always pass
        if kind in ("weather", "terrain", "sky", "blocked"):
            return True

        # No payload = can't check = pass
        if not payload or not isinstance(payload, dict):
            return True

        # ── radio: country code ──
        if kind == "radio":
            p_cc = payload.get("country_code")
            if p_cc and self.cc and p_cc != self.cc:
                return False
            return True

        # ── localcolor: country + culture region ──
        if kind == "localcolor":
            p_cc = payload.get("country_code")
            if p_cc and self.cc and p_cc != self.cc:
                return False
            p_region = payload.get("culture_region")
            if p_region and self.culture_region != "any" and p_region != self.culture_region:
                return False
            return True

        # ── water_features: biome + water type ──
        if kind == "water_features":
            p_biomes = payload.get("biomes")
            if p_biomes and self.biome and self.biome not in p_biomes:
                return False
            p_water = payload.get("water_type")
            if p_water and self.water_type and p_water != self.water_type:
                return False
            return True

        # ── seasonal: season + biome + place ──
        if kind == "seasonal":
            p_seasons = payload.get("seasons")
            if p_seasons and self.season and self.season not in p_seasons:
                return False
            p_biomes = payload.get("biomes")
            if p_biomes and self.biome and self.biome not in p_biomes:
                return False
            p_places = payload.get("places")
            if p_places and self.place and self.place not in p_places:
                return False
            return True

        # ── festival: already filtered internally ──
        if kind == "festival":
            return True

        # ── art / life: culture region + biome ──
        if kind in ("art", "life"):
            p_region = payload.get("culture_region")
            if p_region and self.culture_region != "any" and p_region != self.culture_region:
                return False
            p_biomes = payload.get("biomes")
            if p_biomes and self.biome and self.biome not in p_biomes:
                return False
            return True

        # Default: allow
        return True


# ── Build Situation from server state ──────────────────────────────────

def build_situation(
    lat: float,
    lon: float,
    place: str,
    env: dict[str, Any],
    now_month: int | None = None,
    now_lat: float | None = None,
) -> Situation:
    """Build a Situation from the current position and environment."""
    from nowhere import country
    cc = country.country_code_of(lat, lon)
    season = ""
    if now_month is not None:
        _lat = now_lat if now_lat is not None else lat
        if _lat < 0:
            m = ((now_month - 1 + 6) % 12) + 1
        else:
            m = now_month
        season = ["winter", "winter", "spring", "spring", "spring", "summer",
                  "summer", "summer", "autumn", "autumn", "autumn", "winter"][m - 1]
    biome = None
    # Try to get biome from env or infer from surface
    if "biome" in env:
        biome = env["biome"]
    else:
        _SURFACE_BIOME = {
            "urban": "city", "water_ocean": "coast", "water_fresh": "coast",
            "forest": "rainforest", "sand": "desert", "bare": "desert",
            "snow": "tundra", "ice": "tundra", "rock": "mountain", "grass": "grassland",
        }
        biome = _SURFACE_BIOME.get(env.get("surface", ""), None)
    elev = env.get("elevation", 0.0)
    water_features = env.get("water_features")
    return Situation(
        lat=lat,
        lon=lon,
        place=place,
        cc=cc,
        season=season,
        biome=biome,
        elev=elev,
        climate_zone=_get_climate_zone_for_situation(lat, elev),
        culture_region=_get_culture_region(lat, lon),
        water_type=_infer_water_type(water_features),
    )


_INTENT_MAP: dict[str, dict[str, float]] = {
    "孤独": {"life": 0.5, "radio": 0.5, "sky": 1.5, "terrain": 1.5, "weather": 1.5},
    "安静": {"life": 0.5, "radio": 0.5, "sky": 1.5, "terrain": 1.5, "weather": 1.5},
    "热闹": {"life": 1.5, "radio": 1.5, "water_features": 1.2, "sky": 0.7},
    "人": {"life": 1.5, "radio": 1.5, "water_features": 1.2, "sky": 0.7},
    "水": {"water": 1.5, "water_features": 1.5},
    "海": {"water": 1.5, "water_features": 1.5},
    "古老": {"humanities": 1.5},
    "历史": {"humanities": 1.5},
    "吃": {"localcolor": 2.0},
    "美食": {"localcolor": 2.0},
    "食物": {"localcolor": 2.0},
}

# ── Card 53: gravity — 重力场系数 ─────────────────────────────────────
# 重地 5km 内, humanities 置顶; 轻浮内容降级。
_GRAVITY_HEAVY_BOOST: float = 2.5     # humanities kind boost
_GRAVITY_LIGHT_DEMOTE: float = 0.3    # radio / localcolor demote
_GRAVITY_LIGHT_KINDS: set[str] = {"radio", "localcolor"}


def rank(
    candidates: list[dict],
    rng: random.Random,
    recent_kinds: set[str] | None = None,
    intent: str | None = None,
    heavy_nearby: bool = False,
    situation: Situation | None = None,
) -> list[dict]:
    """Rank candidates by salience and return the top 3.

    Parameters
    ----------
    candidates : list[dict]
        Each dict must have keys: kind, delta, novelty, body_distance, payload.
    rng : random.Random
        Seeded RNG for tie-breaking (reproducible).
    recent_kinds : set[str] | None
        Kinds that appeared in the previous salience result.  Novelty for
        these is multiplied by 0.1 to prevent the same kind winning every
        time when all deltas are zero.
    heavy_nearby : bool
        Card 53: True when within 5km of a heavy place (屠杀/灾难/战争遗址).
        Humanities kind gets boosted, lightweight kinds get demoted.
    situation : Situation | None
        Card 69: Runtime situation filter.  Candidates that don't match the
        current biome/season/place/culture_region are hard-rejected (not
        downranked) before scoring.  This is the primary defense against
        context-mixing (RAG pulling content from wrong location/season).

    Returns
    -------
    list[dict]
        Top-3 candidates sorted by score descending.  Ties broken by rng.
    """
    if not candidates:
        return []

    if recent_kinds is None:
        recent_kinds = set()

    # Card 69: situation filter — hard reject mismatched candidates
    if situation is not None:
        candidates = [c for c in candidates if situation.permits(c)]
        if not candidates:
            return []

    scored = []
    for c in candidates:
        novelty = c["novelty"]
        if c["kind"] in recent_kinds:
            novelty *= 0.1
        score = (
            0.5 * c["delta"]
            + 0.3 * novelty
            + 0.2 * (1.0 - c["body_distance"])
        )
        # Intent bias (Card 12)
        if intent:
            weights = _INTENT_MAP.get(intent, {})
            score *= weights.get(c["kind"], 1.0)
        # Card 53: gravity — 重力场扭曲 salience
        if heavy_nearby:
            if c["kind"] == "humanities":
                score *= _GRAVITY_HEAVY_BOOST
            elif c["kind"] in _GRAVITY_LIGHT_KINDS:
                score *= _GRAVITY_LIGHT_DEMOTE
        scored.append((score, rng.random(), c))

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [t[2] for t in scored[:3]]
