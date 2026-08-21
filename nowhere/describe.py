"""描述引擎——AI 的感官。舌尖上的中国 / 地球脉动旁白声口。

写作规则(验收标准)
-------------------
1. **delta 主轴**: 身体只感觉变化。有 prev 说变化,没有说绝对值,数字必须有。
2. **一次最多三件事**: salience 选 top3,其余闭嘴,留在 data 附件。
3. **名词动词当家**: 形容词一段最多一个,且必须是物理的("松的""潮的"),
   不准是情绪的。空洞程度词禁止出现在本文件源码里(测试把关)。
4. **数字不裸奔**: 整数优先,嵌在文气里("降了 9 度",不写 "29.0°C 的气温")。
5. **物理外推一段最多一句**: 意象必须指得回数据字段("云走得比人快"=风速
   大于步行速度),指不回去就删。
6. **判断句只说世界,不说你**: 允许"在这里,往上是要付代价的",禁止
   替玩家下情绪结论。情绪长在玩家自己身上。
7. **结尾可以不收口**: 一段允许停在悬着的地方。
8. 第二人称,现在时,中文。同 seed 可复现。
"""

from __future__ import annotations

import json
import logging
import pathlib
import random
import re
from typing import Sequence

from nowhere import places

logger = logging.getLogger(__name__)

# ── scene files (literary descriptions per biome/weather) ─────────────
_SCENE_DIR = pathlib.Path(__file__).resolve().parent / "data"
_SCENE_CACHE: dict[str, list[str]] = {}
_WF_SCENES_CACHE: dict | None = None  # water_features_scenes.json

# Valid biome tags for backward-compatible stripping from old scene files
_VALID_BIOME_TAGS: set[str] = {
    "#河", "#湖", "#码头", "#海", "#瀑", "#溪", "#江",
    "#城", "#林", "#漠", "#山", "#极",
}
_BIOME_TAG_RE = re.compile(r"(#[^\s]+)\s*")


# ── location-specific scene files ([地名] 描述 or 地名 描述) ──────────
_LOCATION_SCENES: dict[str, list[str]] | None = None


_LOCATION_SCENES_SEASONAL: dict[tuple[str, str], list[str]] | None = None


def _load_location_scenes() -> dict[str, list[str]]:
    """Load all location-specific scene files.

    Handles two formats:
      - [地名] 描述  (soundscape, taste, china_enhanced)
      - [地名|季] 描述  (season-tagged entries)
      - 地名 描述    (world_enhanced — no brackets)

    Card 82: season-tagged entries are stored in both the main dict
    (by place name) and a seasonal cache keyed by (place, season).
    """
    global _LOCATION_SCENES, _LOCATION_SCENES_SEASONAL
    if _LOCATION_SCENES is not None:
        return _LOCATION_SCENES

    _LOCATION_SCENES = {}
    _LOCATION_SCENES_SEASONAL = {}
    for fname in ["scene_china_enhanced.txt", "scene_world_enhanced.txt",
                   "scene_soundscape.txt", "scene_taste.txt"]:
        fp = _SCENE_DIR / fname
        if not fp.exists():
            continue
        for line in fp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Bracket format: [地名] 描述 or [地名|季] 描述
            if line.startswith("[") and "] " in line:
                bracket_end = line.index("] ")
                bracket_content = line[1:bracket_end]
                desc = line[bracket_end + 2:]
                if "|" in bracket_content:
                    place, season = bracket_content.rsplit("|", 1)
                    _LOCATION_SCENES_SEASONAL.setdefault((place, season), []).append(desc)
                else:
                    place = bracket_content
                _LOCATION_SCENES.setdefault(place, []).append(desc)
            # No-bracket format: 地名 描述 (world_enhanced)
            elif not line.startswith("["):
                sp = line.index(" ") if " " in line else -1
                if sp > 0:
                    place = line[:sp]
                    desc = line[sp + 1:]
                    _LOCATION_SCENES.setdefault(place, []).append(desc)
    return _LOCATION_SCENES


def _get_location_seasonal() -> dict[tuple[str, str], list[str]]:
    """Get seasonal entries from location scene files."""
    global _LOCATION_SCENES_SEASONAL
    if _LOCATION_SCENES_SEASONAL is None:
        _load_location_scenes()
    return _LOCATION_SCENES_SEASONAL or {}


# ── biome-tagged combinatorial scene elements ────────────────────────
_SCENE_ELEMENTS_CACHE: dict | None = None

_SURFACE_TO_BIOME: dict[str, str] = {
    "forest": "forest", "grass": "grassland", "sand": "desert",
    "bare": "desert", "rock": "mountain", "snow": "tundra",
    "ice": "tundra", "water_ocean": "water", "water_fresh": "water",
    "urban": "urban", "wetland": "water",
}

# ── seasonal files (place+season specific descriptions) ──────────────
_SEASONAL_CACHE: dict[tuple[str, str], list[str]] | None = None
_SEASONAL_BIOME_CACHES: dict[str, dict[tuple[str, str], list[str]]] = {}

_SEASON_EN_TO_ZH: dict[str, str] = {
    "spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬",
    "wet": "雨季", "dry": "旱季",
}

_SEASONAL_PATTERN = re.compile(r"\[([^|]+)\|([^\]]+)\]\s*(.+)")


def _parse_seasonal_file(fp: pathlib.Path) -> dict[tuple[str, str], list[str]]:
    """Parse a seasonal file into {(place, season_zh): [descriptions]}."""
    result: dict[tuple[str, str], list[str]] = {}
    for line in fp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _SEASONAL_PATTERN.match(line)
        if m:
            place, season, desc = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
            key = (place, season)
            result.setdefault(key, []).append(desc)
    return result


def _load_seasonal() -> dict[tuple[str, str], list[str]]:
    """Parse seasonal.txt into {(place_or_biome, season_zh): [descriptions]}.

    File format: [城市名|季节] 描述
    """
    global _SEASONAL_CACHE
    if _SEASONAL_CACHE is not None:
        return _SEASONAL_CACHE

    _SEASONAL_CACHE = {}
    seasonal_fp = _SCENE_DIR / "seasonal.txt"
    if seasonal_fp.exists():
        _SEASONAL_CACHE.update(_parse_seasonal_file(seasonal_fp))
    else:
        # Fallback: try old glob pattern
        for fp in _SCENE_DIR.glob("seasonal_*.txt"):
            _SEASONAL_CACHE.update(_parse_seasonal_file(fp))

    return _SEASONAL_CACHE


def _load_seasonal_biome(biome: str) -> dict[tuple[str, str], list[str]]:
    """Load biome-specific seasonal data from seasonal_{biome}.txt.

    These are coast/mountain/etc. entries built at build time,
    filtered by biome so inland cities never see coast sentences.
    """
    if biome not in _SEASONAL_BIOME_CACHES:
        fp = _SCENE_DIR / f"seasonal_{biome}.txt"
        if fp.exists():
            _SEASONAL_BIOME_CACHES[biome] = _parse_seasonal_file(fp)
        else:
            _SEASONAL_BIOME_CACHES[biome] = {}
    return _SEASONAL_BIOME_CACHES[biome]


# Biome-to-seasonal-place mapping for biome-based seasonal entries
_BIOME_TO_SEASONAL_PLACE: dict[str, str] = {
    "rainforest": "热带雨林",
    "desert": "撒哈拉沙漠",
    "tundra": "苔原",
    "mountain": "喜马拉雅/青藏高原",
    "coast": "海岸",
    "island": "海岸",
    "city": "",  # city uses exact place name
    "volcano": "",
    "grassland": "草原",
}

# Tropical rainforest uses different season names
_TROPICAL_SEASON: dict[str, str] = {
    "spring": "干季高峰", "summer": "湿季", "autumn": "过渡", "winter": "干季",
}


def _load_scenes(name: str) -> list[str]:
    """Load scene variants from a scene_*.txt file, one variant per line.

    Card 33: biome-specific product files (built at build time).
    Runtime reads the file directly — zero filtering.

    Backward compat: old files with biome tags (#河, #林, etc.) are still
    supported — tags are stripped from the text.
    """
    if name not in _SCENE_CACHE:
        fp = _SCENE_DIR / f"scene_{name}.txt"
        if fp.exists():
            lines = []
            for l in fp.read_text(encoding="utf-8").splitlines():
                l = l.strip()
                if not l:
                    continue
                # Backward compat: strip biome tags from old-format files
                while True:
                    m = _BIOME_TAG_RE.match(l)
                    if m and m.group(1) in _VALID_BIOME_TAGS:
                        l = l[m.end():]
                    else:
                        break
                # Skip comment lines (start with # but not a biome tag)
                if l.startswith("#"):
                    continue
                # Strip [location] prefix tags from scene text (legacy compat)
                if l.startswith("[") and "] " in l:
                    l = l[l.index("] ") + 2:]
                if l:
                    lines.append(l)
            _SCENE_CACHE[name] = lines
        else:
            _SCENE_CACHE[name] = []
    return _SCENE_CACHE[name]


def _load_scene_elements() -> dict:
    """Load scene_elements.json once and cache."""
    global _SCENE_ELEMENTS_CACHE
    if _SCENE_ELEMENTS_CACHE is None:
        fp = _SCENE_DIR / "scene_elements.json"
        if fp.exists():
            _SCENE_ELEMENTS_CACHE = json.loads(fp.read_text(encoding="utf-8"))
        else:
            _SCENE_ELEMENTS_CACHE = {}
    return _SCENE_ELEMENTS_CACHE


# Region detection for scene filtering
_REGION_MAP = [
    # Specific regions FIRST (more specific beats broader)
    (43, 50, 5, 18, "alpine"),
    (35, 70, -15, 40, "europe"),        # Europe (including Faroe Islands at 62N)
    (45, 70, 20, 180, "russia"),
    (20, 55, 73, 145, "east_asia"),     # Extended to 145°E for Japan
    (-10, 25, 90, 155, "southeast_asia"),
    (5, 35, 60, 100, "south_asia"),
    (10, 45, 25, 65, "middle_east"),
    (-35, 37, -20, 55, "africa"),
    (10, 70, -170, -50, "north_america"),
    (-55, 15, -85, -35, "south_america"),
    (-50, 0, 110, 180, "oceania"),
    # Arctic LAST (catches high-latitude locations not in other regions)
    (66, 90, -180, 180, "arctic"),
]


def _get_region(lat: float, lon: float) -> str:
    """Map coordinates to cultural region for scene filtering."""
    for lat_min, lat_max, lon_min, lon_max, region in _REGION_MAP:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return region
    return "any"


def _compose_walk_scene(surface: str, biome: str, rng: random.Random,
                        lat: float = 0, lon: float = 0,
                        recent_scenes: list[str] | None = None) -> str:
    """Dynamically compose a walk description from biome-tagged elements.

    Each step combines up to 3 elements:
      1. terrain change (60% chance)
      2. sensory detail (always)
      3. discovery (30% chance)
    All elements are biome-tagged to prevent nonsensical combinations.
    recent_scenes: list of recently used scene texts to avoid repetition.
    """
    elements = _load_scene_elements()

    # Map surface to biome key
    biome_key = _SURFACE_TO_BIOME.get(surface, "grassland")

    terrain_pool = elements.get("terrain_change", {}).get(biome_key, [])
    sensory_pool = elements.get("sensory_detail", {}).get(biome_key, [])
    discovery_pool = elements.get("discovery", {}).get(biome_key, [])

    # Filter out recently used scenes (Bug 3: avoid repetition within 3 steps)
    recent = set(recent_scenes or [])
    if recent:
        terrain_pool = [t for t in terrain_pool
                        if (t.get("text", t) if isinstance(t, dict) else t) not in recent]
        sensory_pool = [t for t in sensory_pool
                        if (t.get("text", t) if isinstance(t, dict) else t) not in recent]
        discovery_pool = [t for t in discovery_pool
                          if (t.get("text", t) if isinstance(t, dict) else t) not in recent]

    # Filter tropical-only content for non-tropical latitudes (Bug 2: bamboo)
    abs_lat = abs(lat)
    if abs_lat > 30:  # not tropical
        tropical_keywords = ("竹", "竹林", "藤蔓", "猴子", "热带")
        def _not_tropical(item) -> bool:
            text = item.get("text", "") if isinstance(item, dict) else item
            return not any(k in text for k in tropical_keywords)
        terrain_pool = [t for t in terrain_pool if _not_tropical(t)]
        sensory_pool = [t for t in sensory_pool if _not_tropical(t)]
        discovery_pool = [t for t in discovery_pool if _not_tropical(t)]

    # Filter by region tag (match current location's region)
    region = _get_region(lat, lon) if lat or lon else "any"
    if region != "any":
        def _region_ok(item) -> bool:
            if isinstance(item, dict):
                r = item.get("region", "any")
                return r == "any" or r == region
            return True  # plain strings are universal
        terrain_pool = [t for t in terrain_pool if _region_ok(t)]
        sensory_pool = [t for t in sensory_pool if _region_ok(t)]
        discovery_pool = [t for t in discovery_pool if _region_ok(t)]

    parts: list[str] = []

    def _pick_text(pool: list) -> str:
        """Pick from pool, handling both plain strings and {text, region} dicts."""
        if not pool:
            return ""
        item = rng.choice(pool)
        if isinstance(item, dict):
            return item.get("text", "")
        return item

    # 1. Terrain change (60% chance)
    if terrain_pool and rng.random() < 0.6:
        parts.append(_pick_text(terrain_pool))

    # 2. Sensory detail (always)
    if sensory_pool:
        parts.append(_pick_text(sensory_pool))

    # 3. Discovery (30% chance)
    if discovery_pool and rng.random() < 0.3:
        parts.append(_pick_text(discovery_pool))

    return " ".join(parts) if parts else ""


# ── Scene metadata (structured constraints per line) ─────────────────
_META_CACHE: dict[str, list[dict]] | None = None


def _load_meta() -> dict[str, list[dict]]:
    """Load scene_meta.json once and cache."""
    global _META_CACHE
    if _META_CACHE is None:
        fp = _SCENE_DIR / "scene_meta.json"
        if fp.exists():
            _META_CACHE = json.loads(fp.read_text(encoding="utf-8"))
        else:
            _META_CACHE = {}
    return _META_CACHE


# ── Card 33: scene_card_meta (structured conditions per card text) ────
_CARD_META_CACHE: dict[str, dict] | None = None


def load_scene_card_meta() -> dict[str, dict]:
    """Load scene_card_meta.json (text → {seasons, lat_band, biomes}).

    Built at build time from scenes_src/*.json metadata.
    Used at runtime for structured filtering instead of keyword blacklists.
    """
    global _CARD_META_CACHE
    if _CARD_META_CACHE is None:
        fp = _SCENE_DIR / "scene_card_meta.json"
        if fp.exists():
            _CARD_META_CACHE = json.loads(fp.read_text(encoding="utf-8"))
        else:
            _CARD_META_CACHE = {}
    return _CARD_META_CACHE


def get_card_lat_band(lat: float) -> str:
    """Map latitude to card lat_band category.

    Returns one of: "polar", "north_temperate", "south_temperate", "tropics"
    Matching the values used in scenes_src/*.json metadata.
    """
    abs_lat = abs(lat)
    if abs_lat > 66:
        return "polar"
    if abs_lat < 23.5:
        return "tropics"
    if lat > 0:
        return "north_temperate"
    return "south_temperate"


def filter_by_card_meta(
    pool: list[str],
    current_season: str,
    current_lat: float,
    current_biome: str,
) -> list[str]:
    """Filter a text pool by structured card metadata.

    Cards with seasons restriction: only appear if current_season matches.
    Cards with lat_band restriction: only appear if current lat_band matches.
    Cards with no restrictions (empty arrays): always eligible.
    """
    meta = load_scene_card_meta()
    if not meta:
        return pool  # no meta → no filtering

    current_lat_band = get_card_lat_band(current_lat)
    filtered: list[str] = []

    for text in pool:
        m = meta.get(text)
        if not m:
            # No metadata entry = universal card, always eligible
            filtered.append(text)
            continue

        # Season check
        card_seasons = m.get("seasons", [])
        if card_seasons and current_season not in card_seasons:
            continue

        # Lat band check
        card_lat_band = m.get("lat_band", [])
        if card_lat_band and current_lat_band not in card_lat_band:
            continue

        # Card 72: Biome check
        card_biomes = m.get("biomes", [])
        if card_biomes and current_biome not in card_biomes and "any" not in card_biomes:
            continue

        filtered.append(text)

    return filtered if filtered else pool  # fallback to unfiltered if all removed


def _matches(requires: dict, ctx: dict) -> bool:
    """Check if a scene's constraints are satisfied by the current context.

    ctx keys: season, phase, wind_speed, lat, temp, polar_day, features
    """
    if not requires:
        return True

    # Season constraint
    if "season" in requires:
        if ctx.get("season") not in requires["season"]:
            return False

    # Phase constraint
    if "phase" in requires:
        if ctx.get("phase") not in requires["phase"]:
            return False

    # Wind constraints
    if "wind_max" in requires:
        if (ctx.get("wind_speed") or 0) > requires["wind_max"]:
            return False
    if "wind_min" in requires:
        if (ctx.get("wind_speed") or 0) < requires["wind_min"]:
            return False

    # Polar day constraint
    if "polar_day" in requires:
        if requires["polar_day"] is False and ctx.get("polar_day"):
            return False
        if requires["polar_day"] is True and not ctx.get("polar_day"):
            return False

    # Feature constraint (scene needs a feature the data doesn't have)
    if "feature" in requires:
        feat = requires["feature"]
        features = ctx.get("features") or set()
        if feat not in features:
            return False

    # Latitude constraints
    if "lat_min" in requires:
        if abs(ctx.get("lat") or 0) < requires["lat_min"]:
            return False
    if "lat_max" in requires:
        if abs(ctx.get("lat") or 0) > requires["lat_max"]:
            return False

    # Temperature constraints
    if "temp_min" in requires:
        if (ctx.get("temp") or 999) < requires["temp_min"]:
            return False
    if "temp_max" in requires:
        if (ctx.get("temp") or -999) > requires["temp_max"]:
            return False

    return True


def _location_offset(rng: random.Random, lat: float, lon: float) -> None:
    """Advance RNG state based on location so different places get different scenes.

    This consumes a few random values to shift the RNG sequence, ensuring that
    two locations with the same surface type don't produce identical scene text.
    Deterministic: same (lat, lon) always consumes the same number of values.

    IMPORTANT: Never re-seed the shared module-level rng — that would make all
    subsequent "random" choices a deterministic function of coordinates.
    """
    import hashlib
    h = hashlib.md5(f"{lat:.4f},{lon:.4f}".encode()).hexdigest()
    skip = int(h[:4], 16) % 7  # 0-6 extra random calls
    for _ in range(skip):
        rng.random()


def _pick_scene(pool: list[str], name: str, rng: random.Random, ctx: dict) -> str:
    """Pick a scene from pool, filtering by metadata constraints and biome/altitude."""
    # Biome/altitude filtering
    biome = ctx.get("biome", "")
    lat = ctx.get("lat", 0)
    elev = ctx.get("elevation", 0)
    abs_lat = abs(lat)

    filtered = pool

    # Desert: no water-related scenes
    if biome == "desert":
        desert_bad = ["洒水车", "喷水", "浇花", "水珠", "水溅", "湖面", "鸭子", "泳池", "水帘"]
        filtered = [s for s in filtered if not any(k in s for k in desert_bad)]

    # High altitude (>3000m): no urban/lowland scenes
    if elev > 3000:
        high_bad = ["公园", "湖面", "鸭子", "水泥地", "人行道", "路灯", "便利店", "地铁", "小区", "洒水车"]
        filtered = [s for s in filtered if not any(k in s for k in high_bad)]

    # Non-tropical: no tropical scenes (tropic of cancer/capricorn ~23.5°)
    if abs_lat >= 24:
        tropical_bad = ["椰子", "棕榈", "芭蕉", "热带", "芒果", "榴莲"]
        filtered = [s for s in filtered if not any(k in s for k in tropical_bad)]

    # Non-polar: no snow/ice scenes (only for non-tundra biomes)
    if abs_lat < 50 and biome not in ("tundra",):
        cold_bad = ["雪崩", "冰川", "冻土", "极光", "冰裂缝"]
        filtered = [s for s in filtered if not any(k in s for k in cold_bad)]

    # Card 69: summer/spring — exclude winter-specific scene content
    # Prevents "积雪覆盖的林间小路" in August at 60°N
    season = ctx.get("season", "")
    if season in ("summer", "spring") and biome not in ("tundra", "glacier", "polar"):
        _winter_scene_words = ["下雪", "冰雪", "冰封", "冰面", "冰川", "冰冻", "冻土", "严寒", "积雪", "霜冻"]
        winter_filtered = [s for s in filtered if not any(w in s for w in _winter_scene_words)]
        if winter_filtered:
            filtered = winter_filtered
        elif filtered:
            # All scenes have winter words — return empty (skip this content)
            return ""

    if not filtered:
        filtered = pool  # Fallback to unfiltered if all filtered out

    meta = _load_meta().get(name, [])
    if meta and len(meta) == len(pool):
        # Apply meta constraints against the ORIGINAL pool, then intersect
        # with keyword-filtered results. This avoids the bug where keyword
        # filtering changes len(filtered) so the old len check always fails.
        meta_valid_idx = {i for i, m in enumerate(meta) if _matches(m.get("requires", {}), ctx)}
        if not meta_valid_idx:
            return ""
        pool_to_idx = {s: i for i, s in enumerate(pool)}
        meta_valid_filtered = [s for s in filtered if pool_to_idx.get(s) in meta_valid_idx]
        if meta_valid_filtered:
            return rng.choice(meta_valid_filtered)
        # Meta constraints killed everything after keyword filter — return
        # empty so caller can retry with a different name.
        return ""
    return rng.choice(filtered)


# Map surface/biome to scene file name
_SURFACE_TO_SCENE: dict[str, str] = {
    "sand": "deserts", "bare": "deserts", "rock": "mountains",
    "snow": "snow", "ice": "snow",
    "forest": "forests", "grass": "grasslands",
    "water_ocean": "ocean", "water_fresh": "water_features",
    "urban": "urban", "wetland": "wetland",
}

_WEATHER_TO_SCENE: dict[str, str] = {
    "rain": "rain", "storm": "storm", "snow": "snow", "fog": "fog",
}

_TIME_TO_SCENE: dict[str, str] = {
    "dawn": "dawn", "night": "night", "civil": "dawn",
}

_BIOME_TO_SCENE: dict[str, str] = {
    "volcano": "volcano", "desert": "deserts", "tundra": "tundra",
    "mountain": "mountains", "island": "ocean", "coast": "ocean",
    "rainforest": "forests", "city": "urban",
}

_MOMENT_TO_VISUAL: dict[str, str] = {
    "清晨": "dawn", "凌晨": "dawn", "黎明": "dawn",
    "上午": "day", "正午": "day", "下午": "day", "白天": "day", "白夜": "day",
    "傍晚": "civil", "黄昏": "civil", "不落的黄昏": "civil",
    "暮光": "night", "深夜": "night", "极夜的正午": "night",
}

# ── variant pools (each kind >= 3 variants) ───────────────────────────

_ARRIVE_VARIANTS: list[str] = [
    "你落在{place}。此刻是这里的{时段}。",
    "双脚触到{place}的地面。{时段},一切刚刚开始。",
    "门在身后合上。{place},{时段}。",
    "到了。{place},{时段}的光落在你脚面上。",
    "鞋底踩上{place}的土地。{时段},空气换了味道。",
    "门开了,{place}在眼前。{时段},风从哪个方向来你还不知道。",
    "落地。{place},{时段}。脚下的地跟你出发时不一样。",
    "跨过那道门,{place}。{时段}的光落在手背上。",
]

_WEATHER_ABS_VARIANTS: list[str] = [
    "天{text}着。{temp_c} 度,{humidity_clause}{wind_clause}。",
    "{text}。{temp_c} 度,{humidity_clause}{wind_clause}。",
    "此刻{text},{temp_c} 度。{humidity_clause}{wind_clause}。",
    "{temp_c} 度,{text}。{humidity_clause}{wind_clause},贴着地面走。",
    "天{text}。{humidity_clause}{temp_c} 度,{wind_clause},吹得衣角贴着腿。",
    "{text},{temp_c} 度。{wind_clause},{humidity_clause}鼻尖先知道。",
    "{temp_c} 度,{wind_clause}。{text},{humidity_clause}皮肤上起了一层。",
    "天{text}着,{temp_c} 度。{humidity_clause}{wind_clause},把远处的声音都吹散了。",
]

_WEATHER_RAIN_VARIANTS: list[str] = [
    "雨正下。{temp_c} 度,{wind_clause}。雨声把别的声音都盖住了。",
    "在下雨。{temp_c} 度,雨点砸在{surface_hint}上。{wind_clause}。",
    "雨没有停的意思。{temp_c} 度,{wind_clause},世界只剩雨声。",
    "{temp_c} 度,雨打在脸上。{wind_clause},眼睛睁不开。",
    "雨丝斜着走,{temp_c} 度。{wind_clause},衣服贴在身上,重了。",
    "下雨。{temp_c} 度,{wind_clause}。鞋里灌了水,每一步都咕叽响。",
    "{temp_c} 度,雨落在{surface_hint}上,溅起一层白雾。{wind_clause}。",
    "雨一直下,{temp_c} 度。{wind_clause}。头顶的帽檐滴着水,一串一串。",
]

_WEATHER_SNOW_VARIANTS: list[str] = [
    "雪在下。{temp_c} 度。雪把声音都吃掉了。",
    "下着雪。{temp_c} 度,{wind_clause},雪斜着走。",
    "雪。{temp_c} 度,世界只剩白,和落雪的声音。",
    "{temp_c} 度,雪花落在睫毛上。{wind_clause},每一片都不一样。",
    "雪片大朵大朵地落,{temp_c} 度。{wind_clause},脚印刚踩出来就被填平。",
    "下雪。{temp_c} 度,{wind_clause}。呼出的气在眼前散成白雾。",
    "{temp_c} 度,雪积了一层。{wind_clause},树枝被压弯了。",
    "雪没有要停的意思。{temp_c} 度,{wind_clause}。远处的路被雪盖住了,看不出哪里是路。",
]

_WEATHER_STORM_VARIANTS: list[str] = [
    "雷在响。{temp_c} 度,{wind_clause},空气里有铁味。",
    "雷暴。{temp_c} 度,闪电把天撕开一道。{wind_clause}。",
    "打雷。{temp_c} 度,雨砸下来,云压低了。{wind_clause}。",
    "{temp_c} 度,雷从东边滚过来。{wind_clause},雨横着飞。",
    "闪电劈下来,{temp_c} 度。{wind_clause},把树吹得往一边倒。",
    "雷声在头顶炸开。{temp_c} 度,{wind_clause},雨大得看不见路。",
    "{temp_c} 度,暴风雨。{wind_clause},雨水从领口灌进去。",
    "雷暴来了。{temp_c} 度,{wind_clause}。闪电照出雨帘的形状,然后又黑了。",
]

_WEATHER_DELTA_VARIANTS: list[str] = [
    "{delta_desc}。衣服突然不对了,{text}。{temp_c} 度,{wind_clause}。",
    "{delta_desc}。汗还没干,风已经凉了。{text},{temp_c} 度,{wind_clause}。",
    "出门时的天不长这样。{delta_desc},{text}。{temp_c} 度,{wind_clause}。",
    "{delta_desc},{text}。{temp_c} 度,{wind_clause}。皮肤还没来得及适应。",
    "刚才不是这样。{delta_desc},{text}。{temp_c} 度,{wind_clause}。",
    "{delta_desc}。{text},{temp_c} 度。{wind_clause},跟刚才不一样了。",
    "天翻了脸。{delta_desc}。{text},{temp_c} 度,{wind_clause}。",
    "{text}。{delta_desc}。{temp_c} 度,{wind_clause}。身体比意识先反应过来。",
]

_TERRAIN_VARIANTS: list[str] = [
    "脚下是{surface_desc}{slope_clause}。{elev_clause}。",
    "{surface_desc}{slope_clause},就在脚下。{elev_clause}。",
    "脚下的地是{surface_desc}{slope_clause}。{elev_clause}。",
    "地是{surface_desc}{slope_clause},走起来费力气。{elev_clause}。",
    "每一步踩的都是{surface_desc}{slope_clause}。{elev_clause}。",
    "{surface_desc}{slope_clause},脚底能感觉到地的脾气。{elev_clause}。",
    "坡在{surface_desc}上,{slope_clause}。{elev_clause}。",
    "脚下的{surface_desc}{slope_clause},走一步算一步。{elev_clause}。",
]

_TERRAIN_SCREE_VARIANTS: list[str] = [
    "脚下是{surface_desc}堆的坡,松的。每一步踩下去,都先滑半步,才吃住劲。{elev_clause}。",
    "{surface_desc},松的。坡 {slope_deg} 度,每往上一步,都要付一点代价。{elev_clause}。",
    "坡是{surface_desc}堆出来的,松的,{slope_deg} 度。走一步,滑半步。{elev_clause}。",
    "{surface_desc}在脚下滚,{slope_deg} 度的坡。脚踝扭了一下,你站住了。{elev_clause}。",
    "碎石坡,{slope_deg} 度。{surface_desc}踩一脚滑一脚,登山杖戳进去才稳住。{elev_clause}。",
    "坡上全是{surface_desc},松的。{slope_deg} 度,每一步都要找下脚的地方。{elev_clause}。",
    "{surface_desc},{slope_deg} 度。石头在脚底下响,你听得见自己在滑。{elev_clause}。",
    "往上走,{surface_desc}堆的坡,{slope_deg} 度。脚下的石块一块一块往下溜。{elev_clause}。",
]

_TERRAIN_FLAT_VARIANTS: list[str] = [
    "脚下是{surface_desc},平的,走起来不费力气。{elev_clause}。",
    "地是{surface_desc},平的。{elev_clause}。",
    "{surface_desc}铺开去,远处看不见头。{elev_clause}。",
    "{surface_desc}一路平着走,脚底不用跟地较劲。{elev_clause}。",
    "平的,{surface_desc}。走着走着,容易忘了脚底下。{elev_clause}。",
    "{surface_desc},没有坡。风从远处过来,没有遮挡。{elev_clause}。",
    "脚下的{surface_desc}平得像被人整过。{elev_clause}。",
    "{surface_desc}平展展的,走到哪都一样。{elev_clause}。",
]

_TERRAIN_FLAT_GRASS_VARIANTS: list[str] = [
    "{surface_desc},一马平川。{elev_clause}。",
    "草地平展展的,风一吹像水面。{elev_clause}。",
    "{surface_desc}延伸到天际线。{elev_clause}。",
    "{surface_desc},没有坡。草浪一浪接一浪,从脚下滚到远处。{elev_clause}。",
    "平的,{surface_desc}。风过来了,草全部朝一个方向弯。{elev_clause}。",
    "{surface_desc}铺到天边,中间什么都没有。{elev_clause}。",
    "脚踩在{surface_desc}上,软的,比硬地省力气。{elev_clause}。",
    "{surface_desc},平的。远处有草籽被风吹起来,像一层薄烟。{elev_clause}。",
]

# 裸地/沙地用这些
_TERRAIN_FLAT_BARE_VARIANTS: list[str] = [
    "{surface_desc}一眼望不到头,平的。{elev_clause}。",
    "平的,但脚下的{surface_desc}每一块都不一样。{elev_clause}。",
    "地是{surface_desc},平的,风把什么都吹走了。{elev_clause}。",
    "{surface_desc},平的。脚踩上去,地裂了一道缝。{elev_clause}。",
    "平的,{surface_desc}。风把细沙吹过来,在脚边打转。{elev_clause}。",
    "{surface_desc}铺开去,没有坡,没有遮挡。{elev_clause}。",
    "脚下是{surface_desc},平的。走在上面,脚步声是空的。{elev_clause}。",
    "{surface_desc},一眼看过去全是同一种颜色。{elev_clause}。",
]

_TERRAIN_FLAT_ROCK_VARIANTS: list[str] = [
    "{surface_desc}延伸到远处,平的。{elev_clause}。",
    "碎石平铺,没有坡。{elev_clause}。",
    "岩石平着铺开,风在上面走。{elev_clause}。",
    "{surface_desc},平的。石头的纹路像被人画上去的。{elev_clause}。",
    "平的,{surface_desc}。踩上去脚底是硬的,一步一声响。{elev_clause}。",
    "{surface_desc}平铺,远处有一块大石头歪在那里。{elev_clause}。",
    "脚下是{surface_desc},没有坡。风从石头缝里钻出来。{elev_clause}。",
    "{surface_desc},平的。石头被风磨得发亮,走上去不滑。{elev_clause}。",
]

_TERRAIN_FLAT_URBAN_VARIANTS: list[str] = [
    "硬化路面延伸到远处,平的。{elev_clause}。",
    "马路平的,车在跑。{elev_clause}。",
    "人行道的路沿石被磨得发亮。{elev_clause}。",
    "{surface_desc},平的。脚踩在路面上,声音是实心的。{elev_clause}。",
    "平的,{surface_desc}。路边的梧桐树投下一片影子。{elev_clause}。",
    "{surface_desc}一直铺到看不见的地方。平的。{elev_clause}。",
    "马路平展展的,{surface_desc}。红绿灯在远处闪。{elev_clause}。",
    "脚下是{surface_desc},没有坡。路面的砖缝里长着草。{elev_clause}。",
]

_TERRAIN_FLAT_WATER_VARIANTS: list[str] = [
    "水面平得像镜子。{elev_clause}。",
    "{surface_desc},平的,没有一丝褶皱。{elev_clause}。",
    "水平如镜。{elev_clause}。",
    "{surface_desc},平的。远处有鸟贴着水面飞过。{elev_clause}。",
    "水是平的,{surface_desc}。风吹过来,起了一层细纹。{elev_clause}。",
    "{surface_desc},没有波。你踩进去,水只到脚踝。{elev_clause}。",
    "平的,{surface_desc}。水面倒着天,走过去影子就碎了。{elev_clause}。",
    "{surface_desc}铺开去,平的。水面上浮着一片叶子,不动。{elev_clause}。",
]

_TERRAIN_HIGH_FLAT_VARIANTS: list[str] = [
    "地势平坦,但海拔 {elevation} 米,每一步都喘。脚下是{surface_desc}{delta_clause}。",
    "{surface_desc},平的。可 {elevation} 米的海拔压着胸口,走不快{delta_clause}。",
    "地是{surface_desc},没有坡。但 {elevation} 米的空气稀薄,喘得厉害{delta_clause}。",
    "{elevation} 米,{surface_desc}是平的。呼吸比脚先累{delta_clause}。",
    "平的,{surface_desc}。可 {elevation} 米的空气薄,走三步要停一步{delta_clause}。",
    "脚下是{surface_desc},没有坡。{elevation} 米的海拔,心跳比平时快{delta_clause}。",
    "{surface_desc}平展展的,但 {elevation} 米的空气不够用。走着走着就喘{delta_clause}。",
    "地平,{surface_desc}。{elevation} 米,头有点晕,风从远处过来,没有东西挡{delta_clause}。",
]

_SKY_NIGHT_VARIANTS: list[str] = [
    "天黑了。{moon_str}{planet_str}{milky_str}{aurora_str}",
    "夜沉下来。{moon_str}{planet_str}{milky_str}{aurora_str}",
    "头顶是夜。{moon_str}{planet_str}{milky_str}{aurora_str}",
    "夜铺开了。{moon_str}{planet_str}{milky_str}{aurora_str}",
    "天一黑,{moon_str}{planet_str}{milky_str}{aurora_str}",
    "夜空干净。{moon_str}{planet_str}{milky_str}{aurora_str}",
    "黑下来的天,{moon_str}{planet_str}{milky_str}{aurora_str}",
    "抬头是夜。{moon_str}{planet_str}{milky_str}{aurora_str}",
]

# ── Card 14: Night navigation variants ───────────────────────────────
_NIGHT_NAV_POLAR_LOW: list[str] = [     # lat 10-30
    "北极星贴着地平线,低得快要碰到屋顶。",
    "北极星矮矮的,挂在北边的天际线上。",
    "你找北极星,它在北边低低的地方,像是蹲着。",
    "北边那颗星贴着地平线,你得找个没遮挡的地方才看得见。",
    "北极星在北边低处,仰着头也嫌它矮。",
    "北斗的勺柄弯过去,指着一颗快贴地的星——北极星。",
    "这么低的纬度,北极星几乎在地平线上走。",
    "你往北看,北极星像一盏快灭的灯,挂在天边。",
]
_NIGHT_NAV_POLAR_MID: list[str] = [     # lat 30-50
    "北极星在北边挂着,不高不低,认路的老伙计。",
    "北极星在北天,仰角跟这片纬度一样。",
    "北边那颗不动的星,你已经认识它好些日子了。",
    "北极星挂在半空,不升不降。",
    "北斗七星转了半圈,勺口还是指着北极星。",
    "你往北看,北极星在那,跟你上次看一样高。",
    "北极星不高不低,正好是认路的角度。",
    "夜空里找到北极星,就知道北在哪。今晚它稳稳的。",
]
_NIGHT_NAV_POLAR_HIGH: list[str] = [    # lat 50-66
    "北极星几乎在头顶偏北一点,你仰着脖子看。",
    "银河斜斜地过头顶,你顺着它看北。",
    "北极星高高的,在头顶偏北的地方。",
    "这么高的纬度,北极星快到天顶了。",
    "你仰头看,北极星在头顶偏北一点,够不到。",
    "北极星挂得高,几乎就在头顶。",
    "银河从东到西横过天顶,北极星在它的北边。",
    "北边那颗星今晚特别高,你得把头仰到最大才看得见。",
]
_NIGHT_NAV_POLAR_UNIVERSAL: list[str] = [  # any north lat > 10
    "北边那颗不动的星,今晚格外稳。",
    "星星密得像撒了一把盐,北极星是那颗不动的。",
    "夜空干净,北极星在北边挂着,你认得它。",
    "北斗的勺口指向北极星,你顺着看了一眼。",
    "你找到北极星了。在北边,比别的星都安静。",
    "所有的星都在转,只有北极星不动。你盯着它看了一会儿。",
    "北斗七星在头顶,勺口延长出去,就是北极星。",
    "今晚的北极星比昨晚亮一点。你不确定,但你愿意相信。",
]
_NIGHT_NAV_SOUTHERN: list[str] = [      # lat < -10
    "南十字出来了,长臂指着南天极。",
    "那颗不动的星看不见,但南十字在,南就有了。",
    "四颗星钉成一个十字,你仰头数了两遍。",
    "天顶的星转着圈,南十字是那个锚。",
    "南十字挂在南天,长的一头指向南方。",
    "你认出了南十字,四颗星排成十字架的形状。",
    "南半球的夜空里,南十字是最容易认的星座。",
    "银河从南十字旁边经过,你顺着它找到南。",
]
_NIGHT_NAV_FULL_MOON: list[str] = [     # moon_phase > 0.8
    "满月,影子清楚,不用看脚下。",
    "月光把路照成灰白色,你走得比白天还稳。",
    "这么大的月亮,城里的灯都输了。",
    "满月挂在天上,地上连石头的影子都看得见。",
    "月亮亮得刺眼,星星都躲了。",
    "月光把你的影子拉在地上,长长的,跟在你后面走。",
    "满月。夜里走路跟白天一样,不用打手电。",
    "月亮把远处的山照出轮廓来,你看见了路。",
]
_NIGHT_NAV_NO_MOON: list[str] = [       # moon_phase < 0.2
    "没月亮,黑得慢,你听声音走路。",
    "脚踩在不知道什么东西上,软的,你没低头看。",
    "这么黑,星反而多了,一颗一颗数得过来。",
    "没有月亮,路看不见,你用脚来摸索。",
    "夜黑得厚实,你伸出手看不见手指。",
    "没月亮的夜,星星亮了不少,但照不亮脚下的路。",
    "黑。你听见自己的脚步声和远处不知道什么动物的声音。",
    "天上没有月亮,你靠星光辨认方向。",
]
_NIGHT_NAV_POLAR_NIGHT: list[str] = [   # |lat|>66 winter months
    "太阳不上来,但雪把光存住了。",
    "极夜,天是深蓝的,不是黑的。",
    "月亮和星换着班,你分不清现在几点——只好一直走。",
    "极夜。天没有全黑过,也没有亮过。",
    "太阳不露面,但天边有一条亮的线,一直在那里。",
    "极夜里,雪地反着天光。你分不清是黄昏还是黎明。",
    "已经好多天没见过太阳了。你靠钟表过日子。",
    "极夜的天是深蓝色的,不是黑的。你已经习惯了。",
]


def render_night_nav(
    lat: float,
    moon_phase: float,
    sky_phase: str,
    month: int,
    rng: random.Random,
) -> str | None:
    """Night navigation sentence. Returns None if not applicable.

    Selects based on latitude, moon phase, and polar night conditions.
    Direction words always match actual astronomical bearing.
    """
    if sky_phase not in ("night", "nautical"):
        return None

    abs_lat = abs(lat)

    # Polar night: |lat|>66 and winter months
    is_polar_night = False
    if abs_lat > 66:
        if lat > 0 and month in (11, 12, 1, 2):
            is_polar_night = True
        elif lat < 0 and month in (5, 6, 7):
            is_polar_night = True

    if is_polar_night:
        return rng.choice(_NIGHT_NAV_POLAR_NIGHT)

    # Full moon (checked before hemisphere split)
    if moon_phase > 0.8:
        return rng.choice(_NIGHT_NAV_FULL_MOON)

    # No moon
    if moon_phase < 0.2:
        return rng.choice(_NIGHT_NAV_NO_MOON)

    # Northern hemisphere: polar star
    if lat > 10:
        pool = list(_NIGHT_NAV_POLAR_UNIVERSAL)
        if 10 < lat <= 30:
            pool.extend(_NIGHT_NAV_POLAR_LOW)
        elif 30 < lat <= 50:
            pool.extend(_NIGHT_NAV_POLAR_MID)
        elif 50 < lat <= 66:
            pool.extend(_NIGHT_NAV_POLAR_HIGH)
        return rng.choice(pool)

    # Southern hemisphere: Southern Cross
    if lat < -10:
        return rng.choice(_NIGHT_NAV_SOUTHERN)

    # Near equator: polar star too low, Southern Cross too low
    return None


_SKY_DAY_VARIANTS: list[str] = [
    "太阳在 {sun_alt} 度,光落下来是直的。",
    "日头挂着,{sun_alt} 度。影子缩在脚边。",
    "白天。太阳 {sun_alt} 度,光从天顶附近砸下来。",
    "太阳 {sun_alt} 度,影子踩在脚底下。",
    "{sun_alt} 度的太阳。光把颜色都漂白了。",
    "日头高,{sun_alt} 度。抬头看天,眼睛睁不开。",
    "太阳在 {sun_alt} 度,地面的热气往上蒸。",
    "天亮着,{sun_alt} 度。太阳把影子压成一小块。",
]

_SKY_DAY_LOW_VARIANTS: list[str] = [
    "太阳低着,{sun_alt} 度,影子拉得长。",
    "日头斜了,{sun_alt} 度。地上的影子比实物长。",
    "太阳快贴着地平线了,{sun_alt} 度,光是斜着来的。",
    "太阳 {sun_alt} 度,光斜着打过来,把一切都拉长了。",
    "日头挂在 {sun_alt} 度,影子从脚底下伸出去好远。",
    "太阳低,{sun_alt} 度。光是暖的,但已经没有力气了。",
    "{sun_alt} 度的太阳。地上的影子比人长。",
    "太阳在 {sun_alt} 度,斜斜地照。空气里有金粉。",
]

_WATER_COLD_VARIANTS: list[str] = [
    "海水 {sst} 度。脚踝先麻,然后是针扎。身体比人先记住这片海。",
    "水 {sst} 度。下去的第一秒,呼吸就乱了。",
    "海水 {sst} 度,冷得直接。脚趾先知道,然后是膝盖。",
    "{sst} 度的水。小腿一进去,肌肉就缩了。",
    "水 {sst} 度。脚趾一碰到水面,人就往后退了一步。",
    "海水 {sst} 度,冷得像刀。皮肤发红,牙齿开始打架。",
    "水 {sst} 度。下去之后,胸口像被人按住了。",
    "{sst} 度。水贴在皮肤上,像一层冰。你忍住了。",
]

_WATER_COOL_VARIANTS: list[str] = [
    "海水 {sst} 度。凉,一下一下贴在皮肤上。",
    "水 {sst} 度,凉意顺着脚踝往上爬。",
    "海水 {sst} 度。凉,但能忍,忍过十秒就是自己的了。",
    "{sst} 度的水。凉的,但不刺骨。你站住了。",
    "水 {sst} 度。凉意从脚底往上走,走到腰就停了。",
    "海水 {sst} 度。凉,皮肤上起了一层鸡皮疙瘩。",
    "水 {sst} 度。凉的,像夏天傍晚的风。",
    "{sst} 度。水凉得刚好,不冷不热,泡着舒服。",
]

_WATER_WARM_VARIANTS: list[str] = [
    "水 {sst} 度。皮肤浸下去,像钻进一床棉被。",
    "海水 {sst} 度。远处的浪花发白,脚边的水纹却懒懒的。",
    "{sst} 度的水。什么也不想,就站在里面。",
    "海水 {sst} 度,比体温低一点。整个人像被含住。",
    "水 {sst} 度。童年夏天的河,大概就是这个温度。",
    "{sst} 度。泡到胸口,肩膀露在外面,风吹过来也不觉得冷。",
    "水 {sst} 度。手指在水里张开,能看见,但不重要了。",
    "海水 {sst} 度,和外面的空气几乎没有边界。",
]

_LIFE_VARIANTS: list[str] = [
    "{time_desc},有人在离你 {distance_m} 的地方,遇见过{unit}{common_name}。此刻你不知道它在哪。",
    "{unit}{common_name}。{time_desc},{distance_m}外,有人见过。不知道什么时候的事。",
    "{time_desc},{distance_m}之内,有人遇见过{common_name}。你张望了一下,什么也没看到。",
    "{unit}{common_name}。{distance_m}外。{time_desc}有人见过它,你不知道它现在在哪。",
    "{time_desc},{distance_m}外有人遇见过{common_name}。它也许在,也许不在。你继续走。",
    "{unit}{common_name},{distance_m}。{time_desc}留下的痕迹。你知道它在附近。",
    "{common_name}。{distance_m}之外,{time_desc}有人见过。它还在这里的某处。",
    "{time_desc},{distance_m}外,{unit}{common_name}被记录过。你路过它的领地。",
]

# ── Close-up variants (card 7: short-distance < 0.5km) ──────────────
_CLOSEUP_VARIANTS: list[str] = [
    "脚下的{surface_desc}。十步之内,地面的纹理看得清。",
    "近处的{surface_desc},踩上去能感觉到质地。",
    "就这几步路,{surface_desc}。细节比远处清楚。",
    "脚边的{surface_desc},颗粒分明。",
    "近处地面是{surface_desc}。蹲下来能看见缝隙里的土。",
    "{surface_desc}就在脚下。你能看见每一粒的形状。",
    "低头看,{surface_desc}。脚踩的地方跟旁边不一样。",
    "{surface_desc},细节全在眼前。风吹过,纹理会变。",
]

# Seasonal life encounter variants: (season) → list of templates
_LIFE_SEASONAL: dict[str, list[str]] = {
    "spring": [
        "{common_name}在繁殖,叫声急促,像在叫谁。{dist_str}外。",
        "春天,{common_name}从南方回来了。{dist_str}外,你听见了它的声音。",
        "{unit}{common_name}。{dist_str}外。空气里有花粉的味道。",
        "草地刚返青,{unit}{common_name}在上面走。{dist_str}外。",
        "春天的{dist_str}外,{unit}{common_name}在找吃的。地上有新长的草。",
        "{common_name}从冬眠里醒过来了。{dist_str}外,它在活动。",
        "花开了一片,{unit}{common_name}在花丛里。{dist_str}外。",
        "春雨过后,{unit}{common_name}出来了。{dist_str}外,地上是湿的。",
    ],
    "summer": [
        "{unit}{common_name}在太阳底下活动。{dist_str}外,空气黏在皮肤上。",
        "热,{unit}{common_name}躲在阴凉里。{dist_str}外。",
        "蝉叫得整个林子都在响,{unit}{common_name}从你面前经过。{dist_str}外。",
        "{common_name}。夏天,{dist_str}外,它比你更适应这种热。",
        "{dist_str}外,{unit}{common_name}在水边。天热,它也热。",
        "夏天的傍晚,{unit}{common_name}出来活动了。{dist_str}外。",
        "太阳底下,{unit}{common_name}的影子小小的。{dist_str}外。",
        "{common_name}在树荫里不动。{dist_str}外。热得连虫子都安静了。",
    ],
    "autumn": [
        "{unit}{common_name}在忙着什么。秋天,{dist_str}外,空气凉了。",
        "落叶踩上去沙沙响,{unit}{common_name}在远处。{dist_str}。",
        "{common_name}在囤粮食,{dist_str}外。你知道冬天快来了。",
        "一群鸟往南飞,{unit}{common_name}没走。{dist_str}外。",
        "秋天,{unit}{common_name}比夏天活跃。{dist_str}外,它在准备过冬。",
        "落叶堆里有{common_name}的痕迹。{dist_str}外,它刚走过。",
        "{dist_str}外,{unit}{common_name}在忙着什么。秋天的空气凉飕飕的。",
        "风把叶子吹下来,{unit}{common_name}在树下捡。{dist_str}外。",
    ],
    "winter": [
        "远处有一串脚印,不是人的。你蹲下来看,是{common_name}的。{dist_str}外。",
        "冬天,{unit}{common_name}还在。{dist_str}外。你不知道它怎么过的冬。",
        "雪地上有{common_name}的爪印,新的。{dist_str}外,它刚走过。",
        "{unit}{common_name}。{dist_str}外。冷,但它比你更扛得住。",
        "冬天的{dist_str}外,{unit}{common_name}在雪地里走。它不怕冷。",
        "{common_name}的脚印在雪上,一条线,通向远处。{dist_str}外。",
        "雪地里,{unit}{common_name}的毛色跟白不一样。{dist_str}外。",
        "冬天,{dist_str}外。{unit}{common_name}还在外面,它的呼吸在空气里成白雾。",
    ],
}

# 合并视图: 测试要求每类 ≥3 变体
_WATER_VARIANTS: list[str] = _WATER_COLD_VARIANTS + _WATER_COOL_VARIANTS + _WATER_WARM_VARIANTS

_ART_VARIANTS: list[str] = [
    "此刻应景的一件：{artist}《{title}》。{intro}。{scene}",
    "有一件作品在等你——{artist}《{title}》。{intro}。{scene}",
    "{artist}《{title}》。{intro}。{scene}",
    "这里挂着{artist}的《{title}》。{intro}。{scene}",
    "你面前是{artist}《{title}》。{intro}。{scene}",
    "{artist}画过这地方吗——《{title}》,{intro}。{scene}",
    "有一幅画:{artist}《{title}》。{intro}。{scene}",
    "{artist}《{title}》就在这里。{intro}。{scene}",
]

# 艺术介绍的常用词中译(离线小词典,查不到就略过,不硬翻)
_ART_NATION: dict[str, str] = {
    "American": "美国", "French": "法国", "Dutch": "荷兰", "Italian": "意大利",
    "Japanese": "日本", "British": "英国", "German": "德国", "Spanish": "西班牙",
    "Chinese": "中国", "Dutch, Flemish": "荷兰", "Flemish": "佛兰德斯",
    "Austrian": "奥地利", "Norwegian": "挪威", "Russian": "俄国", "Swiss": "瑞士",
}

_ART_CLASS: dict[str, str] = {
    "Paintings": "绘画", "Prints": "版画", "Photographs": "摄影",
    "Sculpture": "雕塑", "Drawings": "素描", "Textiles": "织物",
    "Ceramics": "陶瓷", "Metalwork": "金工",
}

_ART_MEDIUM: dict[str, str] = {
    "Oil on canvas": "布面油画", "Oil on wood": "木板油画",
    "Watercolor": "水彩", "Etching": "蚀刻版画", "Woodblock print": "木版画",
    "Gelatin silver print": "银盐照片", "Bronze": "青铜", "Ink on paper": "纸本水墨",
}

_ART_TAG: dict[str, str] = {
    "landscape": "风景", "portrait": "人像", "river": "河", "rivers": "河",
    "mountain": "山", "mountains": "山", "tree": "树", "trees": "树",
    "forest": "林子", "snow": "雪", "rain": "雨", "sea": "海", "boat": "船",
    "boats": "船", "sky": "天空", "flowers": "花", "dog": "狗", "horse": "马",
    "horses": "马", "city": "城", "bridge": "桥", "winter": "冬", "summer": "夏",
    "night": "夜", "sunset": "落日", "sunrise": "日出", "woman": "女人",
    "man": "男人", "children": "孩子", "house": "房子", "field": "田野",
    "fields": "田野", "lake": "湖", "water": "水", "clouds": "云",
    "birds": "鸟", "garden": "园子", "street": "街", "window": "窗",
    "women": "女人", "men": "男人", "cat": "猫", "fish": "鱼", "moon": "月亮",
}


def _art_intro(payload: dict) -> str:
    """详细但简短的一句介绍: 作者来头 + 年代 + 门类/媒材 + 画中有什么。
    翻不出中文的字段一律略过,宁缺毋滥。"""
    parts: list[str] = []

    bio = payload.get("artist_bio", "") or ""
    if bio and "," in bio:
        nation_en, _, dates = bio.partition(",")
        nation = _ART_NATION.get(nation_en.strip())
        if nation:
            # bio 尾巴常带地名("Amsterdam 1626–1679 Amsterdam"),只留生卒年
            import re

            m = re.search(r"(\d{3,4})[–—-](\d{3,4})", dates)
            years = f"{m.group(1)}–{m.group(2)}" if m else ""
            parts.append(f"{nation}人,{years}" if years else f"{nation}人")

    year = str(payload.get("year", "")).strip().replace("ca. ", "")
    classification = _ART_CLASS.get(payload.get("classification", ""), "")
    medium_raw = payload.get("medium", "") or ""
    medium = _ART_MEDIUM.get(medium_raw.split(",")[0].strip(), "")

    medium_str = medium or classification
    if year and medium_str:
        parts.append(f"{year} 年的{medium_str}")
    elif medium_str:
        parts.append(medium_str)
    elif year:
        parts.append(str(year))

    tags = payload.get("tags", []) or []
    cn_tags: list[str] = []
    for t in tags:
        zh = _ART_TAG.get(str(t).lower())
        if zh and zh not in cn_tags:
            cn_tags.append(zh)
    if cn_tags:
        parts.append(f"画中是{'、'.join(cn_tags[:3])}")

    if not parts:
        return ""
    return "，".join(parts)

_RADIO_VARIANTS: list[str] = [
    "附近有电台在播。{name},{genre}。",
    "收音机里有声音。{name},正放着{genre}。",
    "{name} 在播{genre}。有人说话的地方,就不算荒。",
    "电台的声音从远处来。{name},{genre}。",
    "你听见了{genre}。{name}在播。",
    "{name}。{genre}。收音机的声音穿过风传过来。",
    "有电台在附近。{name},{genre}。声音不大,但你能分辨出来。",
    "{name}在播{genre}。信号不太稳,有时候会断。",
]

# Card 39: designed quiet during radio cooldown (BotW minimalism)
_RADIO_QUIET_VARIANTS: list[str] = [
    "电台还在,声音小了。",
    "远处有音乐,听不清是什么。",
    "收音机的声音被风盖住了。",
    "电台的信号弱了,像有人在远处说话。",
    "电台的声音飘过来,忽有忽无。",
    "收音机还在响,但声音远了。",
    "电台的频率飘了,只剩下沙沙声。",
    "音乐还在,但你听不清了。风把声音吹散了。",
]

_BLOCKED_VARIANTS: list[str] = [
    "前面是{reason}。走不通,得绕。",
    "{reason}挡在前面。此路不通,换个方向。",
    "路到头了——{reason}。山不让步,人绕。",
    "{reason}。你站住了,看了看左右。",
    "走不了了,{reason}。你得找别的路。",
    "前面是{reason},过不去。你转身。",
    "{reason}横在前面。路断了。",
    "路被{reason}堵住了。你退了两步,重新找方向。",
]

_MESSAGE_VARIANTS: list[str] = [
    "有人在你之前走过这里。他留了一句:「{content}」",
    "路上躺着一句留言:「{content}」——不知道是谁,也不知道是什么时候。",
    "前人经过这里,留下一句:「{content}」",
    "你不是第一个到这的人。有人说:「{content}」",
    "石头上刻着字:「{content}」。你蹲下来看了一会儿。",
    "地上有留言。「{content}」。字迹被风化了一部分。",
    "有人在路边留了一句:「{content}」。你不知道他长什么样。",
    "一块平坦的石头上写着:「{content}」。风吹日晒,字还在。",
]

# ── Farewell variants (card 27: peak-end farewell) ──────────────────

_FAREWELL_VARIANTS: list[str] = [
    "你又看了一眼{place}。然后你转身,门就在那。",
    "鞋底还沾着这里的土。门在身后合上。",
    "你停了一秒。风吹过来,带着这里的味道。然后你走了。",
    "最后看了一眼天。{phase_desc}。门开了。",
    "你把这里的空气吸了一口,转身。门在等。",
    "转身的时候,风从{place}的方向吹过来。你没有回头。",
    "{place}在身后。门开了,你走进去。",
    "你在{place}多站了一会儿。然后你转身,门在等。",
]

_RETURN_VARIANTS: list[str] = [
    "你离开时还是{old_season}，现在{new_season}了。",
    "上次走的时候{old_season}，回来已经是{new_season}。",
    "{old_season}走的，{new_season}回来的。世界没有等你,但也没走远。",
    "{old_season}离开,{new_season}回来。地上的东西换了。",
    "上次是{old_season}。现在{new_season}。你认得这里,但又不太认得。",
    "{old_season}走的时候你没有回头。{new_season}了,你又站在同一个地方。",
    "你记得{old_season}离开时的样子。现在是{new_season},不一样了。",
    "{old_season}到{new_season}。你走了一圈,又回来了。",
]

# ── surface descriptions ─────────────────────────────────────────────

_SURFACE_DESC: dict[str, str] = {
    "rock": "岩石",
    "sand": "沙",
    "snow": "积雪",
    "ice": "冰面",
    "forest": "林地",
    "grass": "草地",
    "urban": "硬化路面",
    "bare": "碎石",
    "wetland": "湿地",
    "water_ocean": "海面",
    "water_fresh": "水面",
}

# ── time period labels ───────────────────────────────────────────────

_TIME_LABELS: dict[str, str] = {
    "day": "白天",
    "civil": "黄昏",
    "nautical": "暮光",
    "night": "深夜",
    "dawn": "黎明",
}


# ── helpers ──────────────────────────────────────────────────────────


def _pick(pool: Sequence[str], rng: random.Random) -> str:
    """Pick one variant from the pool using the seeded rng."""
    return rng.choice(pool)


def _pick_fresh(pool: Sequence[str], rng: random.Random, recent: set[str] | None = None) -> str:
    """Pick from pool, avoiding recently used strings. Falls back to any if all are recent."""
    if not recent:
        return rng.choice(pool)
    fresh = [t for t in pool if t not in recent]
    if fresh:
        return rng.choice(fresh)
    return rng.choice(pool)


def _temp_delta_line(old_temp: float, new_temp: float) -> str:
    diff = round(new_temp - old_temp)
    if diff > 0:
        return f"气温升了 {diff} 度"
    if diff < 0:
        return f"气温降了 {abs(diff)} 度"
    return "气温没变"


def _wind_delta_line(old_wind: float, new_wind: float, rng: random.Random) -> str:
    """Card 39: wind delta → sensory text, never raw numbers."""
    diff = round(new_wind - old_wind)
    if abs(diff) < 2:
        return ""
    if diff > 0:
        return rng.choice(_WIND_DELTA_UP)
    return rng.choice(_WIND_DELTA_DOWN)


def _wind_sensory(wind_ms: float, rng: random.Random) -> str:
    """Card 39: wind speed → sensory clause (no trailing punctuation)."""
    if wind_ms < 1:
        return rng.choice(_WIND_CALM)
    if wind_ms < 4:
        return rng.choice(_WIND_LIGHT)
    if wind_ms < 8:
        return rng.choice(_WIND_MODERATE)
    return rng.choice(_WIND_STRONG)


def _humidity_sensory(feels_c: float, temp_c: float, rng: random.Random) -> str:
    """Card 39: feels_delta → sensory clause (trailing comma or empty).
    Never outputs the raw delta number."""
    diff = round(feels_c - temp_c)
    if diff > 3:
        return rng.choice(_HUMIDITY_HUMID)
    if diff < -3:
        return rng.choice(_HUMIDITY_DRY)
    return ""


# ── public API ───────────────────────────────────────────────────────


def render(
    kind: str,
    payload: dict,
    prev: dict | None,
    rng: random.Random,
    biome: str = "",
    elevation: float = 0,
    recent_scenes: list[str] | None = None,
    recent_touch: set[str] | None = None,
    season: str = "",
    lat: float = 0.0,
) -> str:
    """渲染一种感官。优先用场景文件,兜底用模板。kind 见 _HANDLERS。"""
    # Try scene files for terrain/weather/water
    # Inject biome/elevation into payload for scene selection guards
    if isinstance(payload, dict):
        scene_payload = {**payload, "biome": biome or payload.get("biome", ""), "elevation": elevation or payload.get("elevation", 0)}
    else:
        scene_payload = {"biome": biome, "elevation": elevation}
    scene = _scene_for_kind(kind, scene_payload, rng,
                            lat=scene_payload.get("lat", lat),
                            lon=scene_payload.get("lon", 0.0),
                            recent_scenes=recent_scenes)
    if scene:
        return scene

    handler = _HANDLERS.get(kind)
    if handler is None:
        return ""
    # Set biome context for handlers that need it (e.g. water_features)
    global _CURRENT_BIOME
    _CURRENT_BIOME = biome or ""
    # Card 33: set season/lat context for structured filtering
    global _CURRENT_SEASON, _CURRENT_LAT
    _CURRENT_SEASON = season or ""
    _CURRENT_LAT = lat or 0.0
    # Pass recent_touch to terrain handler via module-level variable
    global _RECENT_TOUCH
    _RECENT_TOUCH = recent_touch or set()
    # Store recent_scenes for dedup across all pools
    global _RECENT_SCENES
    _RECENT_SCENES = recent_scenes or []
    return handler(payload, prev, rng)


def _scene_for_kind(kind: str, payload: dict, rng: random.Random,
                    lat: float = 0.0, lon: float = 0.0,
                    recent_scenes: list[str] | None = None) -> str | None:
    """Try scene files for terrain/weather/water. Combinatorial > location > generic."""
    scene_name = ""
    elevation = payload.get("elevation", 0)
    biome = payload.get("biome", "")
    surface = payload.get("surface", "")

    if kind == "terrain":
        # Skip scene files when payload has specific numeric data --
        # scene files are literary and don't embed numbers like elevation.
        if "elevation" in payload or "slope_deg" in payload:
            return None
        # At high altitude, terrain is specific -- don't use generic scenes
        if elevation and elevation > 3000:
            return None

        # 1. Try combinatorial system first (biome-tagged, region-aware)
        #    Only when biome is set (real walk context, not bare render call)
        if biome and surface:
            composed = _compose_walk_scene(surface, biome, rng, lat, lon,
                                           recent_scenes=recent_scenes)
            if composed:
                return composed

        # 2. Try location-specific scenes (soundscape, taste, china_enhanced, world_enhanced)
        place = payload.get("place", "")
        if place:
            location_scenes = _load_location_scenes()
            if place in location_scenes and rng.random() < 0.5:
                return rng.choice(location_scenes[place])

        # 3. Fall back to generic biome scenes (old scene_*.txt)
        scene_name = _SURFACE_TO_SCENE.get(surface, "")
        # Biome guard: mountain+rock should only use mountain scenes
        if biome == "mountain" and surface == "rock" and scene_name not in ("mountains", ""):
            return None
        # Biome guard: city should only use urban scenes
        if biome == "city" and scene_name not in ("urban", ""):
            return None
        # Biome guard: coast should not get desert scenes (sandy beaches)
        if biome == "coast" and scene_name == "deserts":
            return None
        # Biome guard: tundra should not get desert scenes
        if biome == "tundra" and scene_name == "deserts":
            return None
        # Surface guard: water surfaces should only use water scenes
        if surface in ("water_ocean", "water_fresh") and scene_name != "water":
            return None
    elif kind == "weather":
        precip = payload.get("precip", "none")
        scene_name = _WEATHER_TO_SCENE.get(precip, "")
        # At high altitude, don't use water/river scenes
        if elevation and elevation > 3000 and scene_name in ("water",):
            return None
    elif kind == "water":
        # Skip scene files when payload has specific temperature data
        if "sea_surface_temp" in payload or "sst" in payload:
            return None
        # At high altitude, no rivers/water scenes
        if elevation and elevation > 3000:
            return None
        scene_name = "water"
    elif kind == "water_features":
        # Handled by _render_water_features, not here
        return None
    elif kind == "blocked":
        # Handler needs to embed specific reason; don't use scene files
        return None
    else:
        return None

    if not scene_name:
        return None
    pool = _load_scenes(scene_name)
    if not pool:
        return None
    # Bug 1 fix: filter out urban-specific content for non-urban biomes
    if biome and biome not in ("city", ""):
        _urban_keywords = ("地铁", "胡同", "写字楼", "商场", "广场", "人行道",
                           "马路", "红绿灯", "堵车", "汽车喇叭")
        filtered = [s for s in pool if not any(k in s for k in _urban_keywords)]
        if filtered:
            pool = filtered
    # Location-dependent offset: different places get different scenes
    if lat or lon:
        _location_offset(rng, lat, lon)
    return rng.choice(pool)


def _normalize_prose(text: str) -> str:
    """标点规整: 全半角统一、连续句号压一、多余空格压掉。

    - 全半角统一: 中文字符后的英文逗号/句号/问号/感叹号转全角
    - 连续句号压成一个
    - 多余空格压掉

    注: 缺失句号的修补在 compose() 的拼接点做(按 section 边界处理)。
    """
    if not text:
        return text
    # Half-width → full-width after Chinese character
    text = re.sub(r'(?<=[一-鿿]),', '，', text)
    text = re.sub(r'(?<=[一-鿿])\.', '。', text)
    text = re.sub(r'(?<=[一-鿿])\?', '？', text)
    text = re.sub(r'(?<=[一-鿿])!', '！', text)
    # Consecutive periods → one
    text = re.sub(r'。{2,}', '。', text)
    # Extra spaces
    text = re.sub(r'[ \t]+', ' ', text).strip()
    return text


def _ends_with_cjk(s: str) -> bool:
    """Check if string ends with a CJK character (not punctuation)."""
    if not s:
        return False
    ch = s[-1]
    return bool(re.match(r'[一-鿿]', ch))


def _starts_with_cjk(s: str) -> bool:
    """Check if string starts with a CJK character."""
    if not s:
        return False
    ch = s[0]
    return bool(re.match(r'[一-鿿]', ch))


# Walk-specific transition phrases — only for walk sections
_WALK_TRANSITIONS: list[str] = ["走着走着,", "又走了一段,"]

# ── Card 39: connection word semantic slots ─────────────────────────
# Pools grouped by semantic slot; compose() avoids reusing the same slot.
_TRANSITION_SLOTS_WALK: dict[str, list[str]] = {
    "time": ["紧接着,", "没过多会儿,", "走着走着,"],
    "juxtapose": ["同时,", "这会儿,", "另一边,"],
    "causal": ["于是,", "因此,"],
}
_TRANSITION_SLOTS_ESTABLISH: dict[str, list[str]] = {
    "juxtapose": ["同时,", "这会儿,"],
}

# ── Card 39b: narrative role connector pools ───────────────────────
# 开场/余韵槽为空集——胶水的消失不靠禁,靠结构让胶水无处生根。
_NARRATIVE_ROLES = ("开场", "深入", "转折", "余韵")

_ROLE_CONNECTOR_SLOTS: dict[str, dict[str, list[str]]] = {
    "开场": {},  # no connectors — hard cut
    "深入": {
        "juxtapose": ["同时,", "这会儿,", "另一边,"],
        "time": ["紧接着,", "没过多会儿,", "走着走着,"],
    },
    "转折": {
        "contrast": ["可是,", "不过,", "但是,"],
    },
    "余韵": {},  # no connectors — short independent sentences
}


def _assign_narrative_roles(n: int, rng: random.Random) -> list[str]:
    """Assign a narrative role to each of *n* sections.

    Rules:
      - First section is always 开场 (hard cut, no connectors).
      - Last section: 50% chance 余韵 (short independent sentences).
      - Turning points (转折) placed at ~1/3 and ~2/3 of the sequence.
      - Remaining middle sections: 深入 (deepening).

    Returns a list of role names, one per section.
    """
    roles: list[str] = ["开场"] + ["深入"] * (n - 1)

    if n >= 3:
        # Last section: 50% chance of 余韵
        if rng.random() < 0.5:
            roles[n - 1] = "余韵"

        # Turning points at 1/3 and 2/3 of the sequence
        t1 = max(1, n // 3)
        t2 = max(t1 + 1, 2 * n // 3)

        if t1 < n:
            roles[t1] = "转折"
        if t2 < n:
            roles[t2] = "转折"

    return roles

# ── Card 39: wind sensory pools (≥4 per tier, clause-level, no punctuation) ──
_WIND_CALM: list[str] = [
    "没有风", "一丝风都没有", "空气纹丝不动", "旗子垂着不动",
    "树叶子不动", "烟直直地往上飘", "衣服挂在身上一动不动", "空气凝住了",
]
_WIND_LIGHT: list[str] = [
    "风轻", "微风拂面", "风只够吹动头发", "衣角轻轻动了一下",
    "风擦着脸过去", "草尖晃了一下", "树叶翻了个面又翻回来", "风软的,推不动衣袖",
]
_WIND_MODERATE: list[str] = [
    "风不小", "衣角被吹起来", "风吹着衣摆", "风把帽子往前推了一下",
    "树枝在晃", "头发糊了一脸", "沙粒打在小腿上", "风从领口灌进来",
]
_WIND_STRONG: list[str] = [
    "风猛", "风压着人走", "站不太稳", "风呼呼地响",
    "风把你往前推着走", "睁不开眼", "耳朵里全是风声", "衣服鼓成一面帆",
]

# ── Card 39: humidity sensory pools (≥4 per tier, clause-level, trailing comma) ──
_HUMIDITY_DRY: list[str] = [
    "嘴唇有点干,", "空气干得发紧,", "鼻腔里干的,", "皮肤上像有沙子在磨,",
    "嗓子眼冒烟,", "手指上的倒刺又翘起来了,", "嘴唇裂了一道口子,", "静电啪地打了一下手指,",
]
_HUMIDITY_HUMID: list[str] = [
    "空气黏在皮肤上,", "汗从毛孔里往外渗,", "空气重得能拧出水,", "呼吸像在吸棉花,",
    "衣服贴在背上揭不下来,", "眼镜片起了一层雾,", "手心攥着一把汗,", "后脖颈子湿了一片,",
]

# ── Card 39: wind delta sensory (for weather transitions) ───────────
_WIND_DELTA_UP: list[str] = [
    "风突然大了起来", "风劲了", "风猛了",
    "风一下子灌进领口", "刚才还安静,风说来就来", "树冠猛地一偏,风到了", "头发被风揪起来", "风把衣角掀到脸上",
]
_WIND_DELTA_DOWN: list[str] = [
    "风小了", "风弱了", "风停了些",
    "树梢不再摇", "衣服贴回身上", "风退了,空气又闷起来", "头发落回肩膀", "旗杆上的旗垂下来了",
]


def compose(sections: list[str], rng: random.Random, section_type: str = "walk") -> str:
    """把渲染好的段落拼成一份身体报告。段落间给过渡,但不抢戏。

    Card 39 changes:
    - 连接词概率化: 40% 概率插入,60% 句号硬切; 同段不用同语义槽
    - 段拍变奏: 密段(20%)三景压一段 / 疏段(20%)一句独立成段 / 常(60%)
    - 语义槽分组: 时间/并置/因果, compose 内不复用同槽

    Card 39b: narrative role layer (根治胶水词)
    - 每段生成前先定角色: 开场/深入/转折/余韵
    - 开场/余韵槽为空集——胶水的消失不靠禁,靠结构让胶水无处生根
    - 角色按本步salience排序和段序分配: 首段必开场,末段50%余韵
    """
    sections = [s for s in sections if s and s.strip()]
    if not sections:
        return ""

    n = len(sections)

    # Card 39b: assign narrative roles per section
    roles = _assign_narrative_roles(n, rng)

    parts: list[str] = []
    used_slots: set[str] = set()
    for i, s in enumerate(sections):
        if i == 0:
            parts.append(s)
            continue
        # Insert missing period at section boundary
        if parts and _ends_with_cjk(parts[-1]) and _starts_with_cjk(s):
            parts[-1] += "。"

        role = roles[i]
        slot_pools = _ROLE_CONNECTOR_SLOTS[role]

        t = ""
        if slot_pools:
            # Per-role insertion probability
            prob = {"深入": 0.4, "转折": 0.6}.get(role, 0.15)
            if rng.random() < prob:
                available = [s for s in slot_pools if s not in used_slots]
                if available:
                    slot = rng.choice(available)
                    t = rng.choice(slot_pools[slot])
                    used_slots.add(slot)
        parts.append(t + s)

    # Card 39: paragraph rhythm variation (密/疏/常)
    rhythm_roll = rng.random()
    if section_type == "walk" and len(parts) >= 3:
        if rhythm_roll < 0.2:
            # dense: 三景压一段
            paragraphs = []
            for j in range(0, len(parts), 3):
                paragraphs.append("".join(parts[j:j + 3]))
            result = "\n\n".join(paragraphs)
        elif rhythm_roll < 0.4:
            # sparse: 一句独立成段
            result = "\n\n".join(parts)
        else:
            # normal
            result = "".join(parts)
    else:
        result = "".join(parts)

    # Ensure the final text ends with terminal punctuation
    if result and _ends_with_cjk(result):
        result += "。"
    return _normalize_prose(result)


# ── Card 69: notable place names from all data sources ──────────────
_NOTABLE_PLACES_CACHE: set[str] | None = None


def _load_notable_places() -> set[str]:
    """Load place names from water features scenes and localcolor data.

    These are places that could appear in rendered text from non-location
    sources (water features, localcolor, art, etc.) and need to be checked
    for place contradictions in sanity_check.
    """
    global _NOTABLE_PLACES_CACHE
    if _NOTABLE_PLACES_CACHE is not None:
        return _NOTABLE_PLACES_CACHE

    places: set[str] = set()

    # Water features scenes: top-level keys are place/river names
    try:
        fp = _SCENE_DIR / "water_features_scenes.json"
        if fp.exists():
            import json as _json
            data = _json.loads(fp.read_text(encoding="utf-8"))
            for key in data:
                if isinstance(key, str) and len(key) < 20:
                    places.add(key)
    except Exception:
        logger.debug("failed to load %s", fp)

    # Localcolor files: top-level keys are place names
    for lc_file in _SCENE_DIR.glob("localcolor_*.json"):
        try:
            import json as _json
            data = _json.loads(lc_file.read_text(encoding="utf-8"))
            for key in data:
                if isinstance(key, str) and len(key) < 20:
                    places.add(key)
        except Exception:
            logger.debug("failed to load %s", lc_file)

    _NOTABLE_PLACES_CACHE = places
    return places


def sanity_check(text: str, env: dict) -> str:
    """Last-resort consistency check: fix obvious data-prose contradictions.

    Returns the (possibly patched) text. This is the final safety net,
    not the primary filtering mechanism — scene metadata handles that.

    Card 69: expanded contradiction detection — season words vs season,
    place names vs place, country names vs cc.  Contradicting sentences
    are dropped and replaced with soft fillers.  "宁可少一句,不串一处".
    """
    if not text:
        return text

    weather = env.get("weather") or {}
    sky = env.get("sky") or {}
    terrain = env.get("terrain") or {}

    phase = sky.get("phase", "day")
    precip = weather.get("precip", "none")
    wind = weather.get("wind_ms", 0)
    season = env.get("_season", "")
    biome = env.get("biome", "")
    place = env.get("_place", "")
    cc = env.get("_cc", "")
    temp_c = weather.get("temp_c", 20)

    # Storm: remove calm bird descriptions
    if wind >= 15:
        for bird in ("海鸥蹲", "鸟蹲", "鸽子蹲", "停在桩"):
            if bird in text:
                text = text.replace(bird, "风里有鸟")

    # Night: remove sun references (unless it's about sunset)
    if phase == "night":
        if "太阳" in text and "落" not in text and "没" not in text:
            text = text.replace("太阳", "月亮")

    # Card 71 B4: time_of_day gate — night/dawn ≠ sunset, day ≠ night scenes
    if phase in ("night", "dawn", "nautical"):
        # Dawn/night: no sunset sentences (夕阳/日落/晚霞/残阳)
        _sunset_words = ["夕阳", "日落", "晚霞", "残阳", "落日"]
        if any(w in text for w in _sunset_words):
            text = "天还没亮,你继续走。" if phase == "dawn" else "周围安静下来,你继续走。"
    elif phase == "day":
        # Day: no moon/night sentences (月/星/夜幕/星辰) unless contextually neutral
        _night_words = ["月亮", "月光", "星辰", "夜幕", "星空"]
        if any(w in text for w in _night_words):
            text = "阳光照过来,你眯了眯眼。"

    # Summer: soften frozen/ice references (but keep glacier/polar scenes intact)
    if season in ("summer", "spring"):
        if biome not in ("tundra", "glacier", "polar"):
            for old, new in [("冻住了", "泛着光"), ("冰冻的湖面", "湖面"), ("结冰了", "泛着凉意")]:
                if old in text:
                    text = text.replace(old, new)

    # ── Card 69: expanded sentence-level contradiction detection ───────
    # Split into sentences, check each for contradictions.
    # Contradicting sentences are replaced with soft fillers.
    _SOFT_FILLERS = [
        "你顿了顿,又看了一眼。",
        "风吹过来,你回过神。",
        "脚步没停。",
        "你眨了眨眼,继续走。",
        "空气里有什么变了,你说不上来。",
    ]

    # Build location scene keys for place contradiction detection
    _location_scenes = _load_location_scenes()
    _scene_places = set(_location_scenes.keys())
    # Expand with place names from water features scenes and localcolor
    _scene_places.update(_load_notable_places())

    # Country name lookup (reverse of _COUNTRY_ZH)
    _cc_to_name: dict[str, str] = {}
    for _code, _name in _COUNTRY_ZH.items():
        _cc_to_name[_code] = _name

    sentences = _split_sentences(text)

    changed = False
    filler_idx = 0

    for i, sent in enumerate(sentences):
        if not sent.strip():
            continue

        # ── Season contradiction ──
        if season in ("summer", "spring") and biome not in ("tundra", "glacier", "polar"):
            _winter_words = ["下雪", "冰雪", "冰封", "冰面", "冰川", "冰冻", "冻土", "严寒", "积雪", "霜冻"]
            if any(w in sent for w in _winter_words):
                # Allow if temperature is genuinely cold (< 5°C)
                if temp_c >= 5:
                    sentences[i] = _SOFT_FILLERS[filler_idx % len(_SOFT_FILLERS)]
                    filler_idx += 1
                    changed = True
                    continue

        # ── Place name contradiction ──
        if place and _scene_places:
            wrong_places = [p for p in _scene_places if p in sent and p != place]
            if wrong_places:
                sentences[i] = _SOFT_FILLERS[filler_idx % len(_SOFT_FILLERS)]
                filler_idx += 1
                changed = True
                continue

        # ── Country name contradiction ──
        if cc:
            current_country_name = _cc_to_name.get(cc, "")
            for other_cc, other_name in _cc_to_name.items():
                if other_cc == cc:
                    continue
                if other_name in sent and current_country_name not in sent:
                    sentences[i] = _SOFT_FILLERS[filler_idx % len(_SOFT_FILLERS)]
                    filler_idx += 1
                    changed = True
                    break

    if not changed:
        return text

    result = "".join(sentences)
    if result and not result.endswith(("。", "！", "？", "」")):
        result += "。"
    return result


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences at CJK sentence boundaries.

    Keeps the punctuation with each sentence.
    """
    if not text:
        return [text]
    parts = re.split(r'(?<=[。！？])', text)
    return [p for p in parts if p]


# ── per-kind renderers ───────────────────────────────────────────────


def _render_arrive(payload: dict, prev: dict | None, rng: random.Random) -> str:
    place = payload.get("place", "未知之地")
    period = payload.get("时段", payload.get("period", "白天"))
    label = _TIME_LABELS.get(period, period)
    tmpl = _pick(_ARRIVE_VARIANTS, rng)
    return tmpl.format(place=place, 时段=label)


def _render_weather(payload: dict, prev: dict | None, rng: random.Random) -> str:
    temp_c = round(payload["temp_c"])
    feels_c = payload["feels_c"]
    wind_ms = round(payload["wind_ms"])
    text = payload.get("text", "")
    precip = payload.get("precip", "none")

    # Card 39: sensory rendering — wind/humidity clauses, no raw feels_delta
    wind_clause = _wind_sensory(wind_ms, rng)
    humidity_clause = _humidity_sensory(feels_c, payload["temp_c"], rng)

    # 物理外推: 风速超过步行速度(~1.1m/s)的八倍且有云,云比人快
    cloudy = any(w in text for w in ("云", "阴"))
    wind_tail = "。这样的风里,云走得比人快" if (wind_ms >= 9 and cloudy) else ""

    prev_weather = (prev or {}).get("weather")
    if prev_weather is not None:
        old_temp = prev_weather.get("temp_c", payload["temp_c"])
        old_wind = prev_weather.get("wind_ms", payload["wind_ms"])
        delta_desc = _temp_delta_line(old_temp, payload["temp_c"])
        wind_line = _wind_delta_line(old_wind, payload["wind_ms"], rng)
        if wind_line:
            delta_desc += "," + wind_line
        tmpl = _pick(_WEATHER_DELTA_VARIANTS, rng)
        return tmpl.format(temp_c=temp_c, wind_clause=wind_clause, text=text, delta_desc=delta_desc) + wind_tail

    if precip == "rain":
        tmpl = _pick(_WEATHER_RAIN_VARIANTS, rng)
        return tmpl.format(temp_c=temp_c, wind_clause=wind_clause, surface_hint="地")
    if precip == "snow":
        tmpl = _pick(_WEATHER_SNOW_VARIANTS, rng)
        return tmpl.format(temp_c=temp_c, wind_clause=wind_clause)
    if precip == "storm":
        tmpl = _pick(_WEATHER_STORM_VARIANTS, rng)
        return tmpl.format(temp_c=temp_c, wind_clause=wind_clause)

    tmpl = _pick(_WEATHER_ABS_VARIANTS, rng)
    result = tmpl.format(
        temp_c=temp_c,
        text=text or "晴",
        humidity_clause=humidity_clause,
        wind_clause=wind_clause,
    )
    return result + wind_tail


def _render_terrain(payload: dict, prev: dict | None, rng: random.Random) -> str:
    surface_key = payload.get("surface", "rock")
    slope_deg = payload.get("slope_deg", 0)
    elevation = round(payload.get("elevation", 0))
    elevation_delta = payload.get("elevation_delta", 0)
    biome = payload.get("biome", "")

    surface_desc = _SURFACE_DESC.get(surface_key, surface_key)

    # 海拔只在"变了"或"本身高"时提, 平原复读时省略, 避免每步"海拔 300 米"
    d_round = round(elevation_delta)  # 先四舍五入, 过滤网格的亚米级噪声
    if d_round > 0:
        elev_clause = f"海拔 {elevation} 米,又抬高了 {d_round} 米"
    elif d_round < 0:
        elev_clause = f"海拔 {elevation} 米,又落下了 {abs(d_round)} 米"
    elif elevation >= 1000:
        elev_clause = f"海拔 {elevation} 米"
    else:
        elev_clause = ""
    delta_clause = (
        f",又抬高了 {round(elevation_delta)} 米" if elevation_delta > 0
        else f",又落下了 {abs(round(elevation_delta))} 米" if elevation_delta < 0
        else ""
    )

    result = ""

    if surface_key in ("rock", "bare") and slope_deg > 15:
        tmpl = _pick(_TERRAIN_SCREE_VARIANTS, rng)
        result = tmpl.format(
            surface_desc=surface_desc,
            slope_deg=round(slope_deg),
            elev_clause=elev_clause,
        )
    elif slope_deg < 1.0:
        if elevation > 2500:
            # 高海拔变体把"稀薄/喘"当主题, 海拔必须提
            tmpl = _pick(_TERRAIN_HIGH_FLAT_VARIANTS, rng)
            result = tmpl.format(
                surface_desc=surface_desc,
                elevation=elevation,
                delta_clause=delta_clause,
            )
        else:
            if surface_key in ("bare", "sand"):
                tmpl = _pick(_TERRAIN_FLAT_BARE_VARIANTS, rng)
            elif surface_key == "rock":
                tmpl = _pick(_TERRAIN_FLAT_ROCK_VARIANTS, rng)
            elif surface_key == "urban":
                tmpl = _pick(_TERRAIN_FLAT_URBAN_VARIANTS, rng)
            elif surface_key in ("water_ocean", "water_fresh"):
                tmpl = _pick(_TERRAIN_FLAT_WATER_VARIANTS, rng)
            elif surface_key == "grass":
                tmpl = _pick(_TERRAIN_FLAT_GRASS_VARIANTS, rng)
            else:
                tmpl = _pick(_TERRAIN_FLAT_VARIANTS, rng)
            result = tmpl.format(
                surface_desc=surface_desc,
                elev_clause=elev_clause,
            )
    else:
        slope_clause = f",坡 {round(slope_deg)} 度"
        tmpl = _pick(_TERRAIN_VARIANTS, rng)
        result = tmpl.format(
            surface_desc=surface_desc,
            slope_clause=slope_clause,
            elev_clause=elev_clause,
        )

    # Append touch description (filter out recently used)
    touch_pool = _TOUCH_BY_SURFACE.get(surface_key, [])
    if touch_pool:
        recent = _RECENT_TOUCH | set(_RECENT_SCENES[-10:])
        if recent:
            fresh = [t for t in touch_pool if t not in recent]
            if fresh:
                touch_pool = fresh
        pick = rng.choice(touch_pool)
        result += pick + "。"

    # Append smell description (filter out recently used)
    smell_pool = _SMELL_BY_BIOME.get(biome, _SMELL_BY_BIOME.get(surface_key, []))
    if smell_pool:
        recent = _RECENT_TOUCH | set(_RECENT_SCENES[-10:])
        if recent:
            fresh = [s for s in smell_pool if s not in recent]
            if fresh:
                smell_pool = fresh
        result += rng.choice(smell_pool) + "。"

    # 海拔省略后模板可能留下"。。"
    result = result.replace("。。", "。")

    return result


def _render_sky(payload: dict, prev: dict | None, rng: random.Random) -> str:
    phase = payload.get("phase", "day")
    sun_alt = payload.get("sun_alt", 0)

    if phase == "night" or sun_alt < 0:
        moon_phase = payload.get("moon_phase", 0)
        moon_alt = payload.get("moon_alt", -90)
        planets: list[dict] = payload.get("planets", [])
        milky_up = payload.get("milky_way_core_up", False)

        moon_str = ""
        if moon_alt > 0:
            if moon_phase > 0.8:
                moon_str = "满月。在这样的夜里,影子比任何夜晚都清楚。"
            elif moon_phase > 0.4:
                moon_str = f"月亮在 {round(moon_alt)} 度,亮了大半。"
            else:
                moon_str = f"一弯月牙,挂在 {round(moon_alt)} 度。"

        planet_str = ""
        for p in planets[:2]:
            name_cn = {
                "Mercury": "水星",
                "Venus": "金星",
                "Mars": "火星",
                "Jupiter": "木星",
                "Saturn": "土星",
            }.get(p["name"], p["name"])
            planet_str += f"{name_cn}挂在那里,{round(p.get('alt', 0))} 度高。"

        milky_str = ""
        if milky_up:
            milky_str = "银心刚升起来,斜斜的一条。"

        aurora_str = ""
        aurora = payload.get("aurora")
        if aurora:
            color = aurora["color"]
            shape = aurora["shape"]
            intensity = aurora["intensity"]
            color_desc = {
                "green": "绿的",
                "green_purple": "绿里带紫",
                "purple_red": "紫红色",
            }.get(color, "绿的")
            shape_desc = {
                "arc": "一道弧",
                "curtain": "像帘子一样垂下来",
                "corona": "从天顶散开,像个光冠",
                "diffuse": "一片弥散的光",
            }.get(shape, "一道弧")
            if intensity >= 4:
                aurora_str = f"极光来了,{color_desc},{shape_desc},动得急,整个天空都在抖。"
            elif intensity >= 2:
                aurora_str = f"极光,{color_desc},{shape_desc},慢慢地动。"
            else:
                aurora_str = f"天边有极光,淡淡的{color_desc},{shape_desc}。"

        if not moon_str and not planet_str and not milky_str and not aurora_str:
            moon_str = "无月。星星倒是一颗不少。"

        recent = set(_RECENT_SCENES[-10:]) if _RECENT_SCENES else set()
        tmpl = _pick_fresh(_SKY_NIGHT_VARIANTS, rng, recent)
        return tmpl.format(moon_str=moon_str, planet_str=planet_str, milky_str=milky_str, aurora_str=aurora_str)

    sun_alt_r = round(sun_alt)
    recent = set(_RECENT_SCENES[-10:]) if _RECENT_SCENES else set()
    if sun_alt_r < 15:
        tmpl = _pick_fresh(_SKY_DAY_LOW_VARIANTS, rng, recent)
    else:
        tmpl = _pick_fresh(_SKY_DAY_VARIANTS, rng, recent)
    return tmpl.format(sun_alt=sun_alt_r)


def _render_water(payload: dict, prev: dict | None, rng: random.Random) -> str:
    sst = round(payload.get("sea_surface_temp", payload.get("sst", 20)))
    recent = set(_RECENT_SCENES[-10:]) if _RECENT_SCENES else set()
    if sst < 10:
        tmpl = _pick_fresh(_WATER_COLD_VARIANTS, rng, recent)
    elif sst < 22:
        tmpl = _pick_fresh(_WATER_COOL_VARIANTS, rng, recent)
    else:
        tmpl = _pick_fresh(_WATER_WARM_VARIANTS, rng, recent)
    return tmpl.format(sst=sst)


def _render_life(payload: dict, prev: dict | None, rng: random.Random) -> str:
    common_name = payload.get("common_name", "未知生物")
    distance_m = payload.get("distance_m") or 100
    seen_at = payload.get("seen_at", "")
    unit = payload.get("unit", "一只")
    time_desc = seen_at if seen_at else "不久前"
    season = payload.get("season", "")
    biome = payload.get("biome", "")

    # Format distance naturally
    if distance_m >= 1000:
        dist_str = f"{distance_m / 1000:.1f} 公里".replace(".0 ", " ")
    else:
        dist_str = f"{round(distance_m)} 米"

    # For plants, try seasonal plant scene file
    is_plant = unit == "一棵"
    if is_plant:
        plant_pool = _load_scenes("plants")
        if plant_pool and rng.random() < 0.6:
            # Filter tropical-only plants for non-tropical biomes
            cur_biome = _CURRENT_BIOME
            if cur_biome and cur_biome not in ("rainforest", ""):
                _tropical_plant_kw = ("竹", "藤", "椰子", "芭蕉", "热带")
                filtered = [p for p in plant_pool
                            if not any(k in p for k in _tropical_plant_kw)]
                if filtered:
                    plant_pool = filtered
            scene = rng.choice(plant_pool)
            return f"{common_name}。{dist_str}外。{scene}"

    # Try life scene file (30% chance, lower than before to let seasonal shine)
    life_pool = _load_scenes("life")
    if life_pool and rng.random() < 0.3:
        scene = rng.choice(life_pool)
        return f"{common_name}。{dist_str}外。{scene}"

    # Use seasonal variants (50% chance when season is known)
    if season and season in _LIFE_SEASONAL and rng.random() < 0.5:
        tmpl = rng.choice(_LIFE_SEASONAL[season])
        return tmpl.format(
            common_name=common_name,
            dist_str=dist_str,
            unit=unit,
        )

    # Fallback to generic variants
    tmpl = _pick(_LIFE_VARIANTS, rng)
    return tmpl.format(
        common_name=common_name,
        distance_m=dist_str,
        time_desc=time_desc,
        unit=unit,
    )


_ART_SCENE: list[str] = [
    "站在这儿看它，比在美术馆里近。",
    "画面里的光和此刻的光，隔着几百年，但温度差不多。",
    "不知道是它映了这地方，还是这地方映了它。",
    "在这儿遇见它，像是被安排的。",
    "原作不在这里，但感觉在。",
    "光打在画上的角度，跟旁边的影子对上了。",
    "画里的风景跟眼前的风景，隔着时间和画布，但有些东西是一样的。",
    "你在这儿看这幅画，跟在美术馆里看，是两种不同的事。",
]


def _render_art(payload: dict, prev: dict | None, rng: random.Random) -> str:
    title = payload.get("title", "无题")
    artist = payload.get("artist", "佚名")
    zim = payload.get("zim_extract")

    if zim:
        # Use real Wikipedia interpretation — truncate at sentence boundary
        extract = zim[:200]
        for sep in ("。", ".", "！", "!", "？", "?"):
            idx = extract.rfind(sep)
            if idx > 50:
                extract = extract[: idx + 1]
                break
        return f"{artist}《{title}》。{extract}"

    # Fallback to Met metadata + scene
    intro = _art_intro(payload)
    scene = _pick(_ART_SCENE, rng)
    tmpl = _pick(_ART_VARIANTS, rng)
    return tmpl.format(title=title, artist=artist, intro=intro, scene=scene)


_GENRE_ZH: dict[str, str] = {
    "news": "新闻", "pop": "流行", "top 40": "热门金曲", "rock": "摇滚",
    "jazz": "爵士", "classical": "古典", "dance": "舞曲", "electronic": "电子",
    "folk": "民谣", "country": "乡村", "talk": "谈话", "sports": "体育",
    "oldies": "老歌", "hits": "热门", "asian pop": "亚洲流行", "k-pop": "韩流",
    "j-pop": "日系流行", "hip hop": "嘻哈", "rap": "说唱", "reggae": "雷鬼",
    "blues": "布鲁斯", "soul": "灵魂乐", "ambient": "氛围", "chillout": "弛放",
    "latin": "拉丁", "world": "世界音乐", "gospel": "福音", "metal": "金属",
    "music": "音乐", "pop music": "流行", "local music": "本地音乐",
    "classic hits": "经典热门", "adult contemporary": "成人当代",
    "eclectic": "杂糅", "eclectic/news": "杂糅新闻", "variety": "综合",
}


def _genre_zh(genre: str) -> str:
    """电台流派标签中译,查不到的原样保留。"""
    if not genre:
        return "音乐"
    # 逗号和斜杠都算分隔, 让 "eclectic/news" 拆成两个词各自翻译
    parts = [g.strip() for g in genre.replace("/", ",").split(",") if g.strip()]
    zh = [_GENRE_ZH.get(g.lower(), g) for g in parts[:3]]
    return "、".join(zh) if zh else "音乐"


def _render_radio(payload: dict, prev: dict | None, rng: random.Random) -> str:
    name = payload.get("name", "未知电台")
    genre = _genre_zh(payload.get("genre", ""))
    tmpl = _pick(_RADIO_VARIANTS, rng)
    return tmpl.format(name=name, genre=genre)


def _render_blocked(payload: dict, prev: dict | None, rng: random.Random) -> str:
    reason = payload.get("reason", "障碍")
    tmpl = _pick(_BLOCKED_VARIANTS, rng)
    return tmpl.format(reason=reason)


def _render_message(payload: dict, prev: dict | None, rng: random.Random) -> str:
    content = payload.get("content", "")
    tmpl = _pick(_MESSAGE_VARIANTS, rng)
    return tmpl.format(content=content)


# ── 开幕镜头(open_door 专用)────────────────────────────────────────
# 文体规则(旋复定的):
# 1. 美来自精确不来自华丽: 名词动词当家;浮夸词清单在测试里,本文件不得出现
# 2. 探索感来自未完成: 局部切入 1-3 个碎片,不做全景概述
# 3. 结尾必带钩子: 暗示还有可看可听的,钩子必须从数据来
# 4. ≤200 字,头部【国家,地名,时刻】

_ESTABLISH_VISUAL: dict[str, list[str]] = {
    "day": [
        "光铺满{surface_zh},{shape}。",
        "白昼的光从头顶下来,{surface_zh}上没有藏东西的地方。",
        "太阳在天上,{surface_zh}被照得发白。{shape}。",
        "光把{surface_zh}的颜色漂淡了。{shape}。",
        "天亮着,{surface_zh}在光底下,{shape}。",
        "阳光砸在{surface_zh}上,{shape}。",
        "{surface_zh},白天。{shape},光从头顶压下来。",
        "光落下来,{surface_zh}上什么都看得见。{shape}。",
    ],
    "civil": [
        "天边的光斜过来,{surface_zh}的影子都拉长了。",
        "橘红色的天边,{shape}成了剪影。",
        "黄昏。{surface_zh}的轮廓在光里模糊了,{shape}。",
        "天边烧起来了,{surface_zh}染成橘红色。{shape}。",
        "最后一缕光落在{surface_zh}上,{shape}。",
        "黄昏的光是斜的,{surface_zh}上全是长影子。{shape}。",
        "天边的云烧红了,{surface_zh}在底下,{shape}。",
        "太阳在地平线上,{surface_zh}被染成两种颜色。{shape}。",
    ],
    "night": [
        "天黑了,{surface_zh}沉进夜色里,只有{light}还亮着。",
        "夜把{surface_zh}收走了,{light}是仅剩的坐标。",
        "黑下来的{surface_zh},{light}在远处闪。",
        "夜,{surface_zh}看不见了。{light}在黑暗里挂不住。",
        "天一黑,{surface_zh}就没了。{light}是唯一的亮。",
        "{surface_zh}沉进夜色,{light}在远处。",
        "夜里,{surface_zh}变成一个影子,{light}是它的边界。",
        "夜把{surface_zh}吞了,{light}从缝隙里漏出来。",
    ],
    "dawn": [
        "天边刚撕开一条缝,光先落在{surface_zh}的尖上。",
        "晨雾还没散,{shape}在雾里浮着。",
        "黎明。{surface_zh}在灰白色的光里醒过来。{shape}。",
        "天边有光了,{surface_zh}从黑变成灰。{shape}。",
        "雾里,{surface_zh}的轮廓刚看得见。{shape}。",
        "第一缕光落在{surface_zh}上。{shape},一切都还在。",
        "黎明的光薄薄的,{surface_zh}在雾里。{shape}。",
        "天边亮了,{surface_zh}从夜色里走出来。{shape}。",
    ],
}

# 视觉/地名用——比 _SURFACE_DESC 更简洁（"雪原" vs "冻硬的雪壳"）。
# soundscape.py 和 server.py 均从此处导入，不再各自维护副本。
_SURFACE_ZH: dict[str, str] = {
    "rock": "岩石", "sand": "沙", "snow": "雪原", "ice": "冰面",
    "forest": "林子", "grass": "草原", "urban": "城", "bare": "碎石滩",
    "wetland": "湿地", "water_ocean": "海", "water_fresh": "水面",
}

# 视觉的形状词: 每种地表的默认画面
_SHAPE_BY_SURFACE: dict[str, str] = {
    "forest": "树一层压着一层",
    "water_ocean": "水一直铺到天边",
    "water_fresh": "水面平着,光在上面碎",
    "urban": "房子挤着房子",
    "sand": "沙丘一道一道,像凝固的浪",
    "rock": "石头黑着脸",
    "grass": "草一直铺到看不清的地方",
    "snow": "白连成一片,没有边",
    "ice": "冰面亮得晃眼",
    "bare": "碎石铺到天边",
    "wetland": "水草相间,鸟贴着飞",
}

_SMELL_BY_PRECIP: dict[str, str] = {
    "rain": "空气里是土腥味,雨后的那种,厚的。",
    "snow": "空气冷而干净,什么味道都被雪收走了。",
}

_SMELL_BY_SURFACE: dict[str, str] = {
    "water_ocean": "风里有咸味,海的味道,先鼻子后眼睛。",
    "forest": "空气里是叶子和腐殖土的味道,潮的。",
    "sand": "空气干得发紧,尘土的味道。",
    "urban": "空气里是烟火气,有人间在附近。",
}

_SMELL_BY_BIOME: dict[str, list[str]] = {
    "rainforest": ["腐叶的甜味混着泥土的腥", "空气黏在皮肤上，带着花香和霉味", "潮湿的木头味道", "芭蕉叶被太阳晒出的青气", "树干渗出的树脂味，黏的"],
    "desert": ["干燥的热气，没有味道但你闻到了'干'", "沙子被太阳烤过的味道", "远处有植物烧焦的味道", "皮革和灰尘的味道，干的", "风卷起来的沙尘，呛鼻子"],
    "tundra": ["冷空气，干净的，带一点金属", "苔藓的味道，湿的，像刚下过雪", "冻土化开的泥腥味", "地衣被风吹散的味道，干的", "冰面裂开时散出的冷气"],
    "mountain": ["稀薄的空气，闻起来什么都没有", "岩石被太阳晒热的味道", "远处有雪的味道，冷的", "松针的味道，干的，从山腰飘来", "风里带一点草药的苦"],
    "coast": ["海盐混着海藻的腥", "鱼的味道，淡的，被风吹散了", "潮湿的木头，码头的味道", "海藻腐烂的味道，浓的", "铁锈味，锚链上的"],
    "city": ["油烟和香料的味道", "汽车尾气混着烤面包的味道", "街角飘来咖啡和烤面包的香气", "下水道的潮气，淡淡的", "旧墙皮被雨打湿的味道"],
    "grassland": ["青草碾碎的味道", "干草的味道，暖的", "远处有篝火的烟味", "土被太阳晒过的味道，暖的", "马粪的味道，远的，被风稀释了"],
    "volcano": ["硫磺的味道，刺鼻", "热石头的味道，像铁", "蒸汽带着矿物质的涩", "烧焦的泥土味，干的", "热泉冒出来的水汽，带铁腥"],
    "wetland": ["腐殖质的味道，浓的", "水草的腥味", "泥巴的味道，潮的", "芦苇秆折断的味道，青的", "水里带铁锈味，腥的"],
    "snow": ["冷空气，干净得发苦", "雪化成水的味道，带一点泥土", "风里什么都没有，但你知道那是雪", "雪落在衣服上的味道，冷的", "树皮被冻住的味道，干的"],
    "water": ["水面飘来潮润的土腥", "水汽带着泥腥", "空气湿得能拧出水", "水草泡烂的味道，腥的", "岸边泥土的味道，湿的"],
}

_TOUCH_BY_SURFACE: dict[str, list[str]] = {
    "sand": [
        "脚踩下去，沙子陷了半寸",
        "沙子从脚趾缝里挤出来",
        "热沙隔着鞋底烫上来",
        "每一步踩下去，沙子都往两边塌",
        "脚踝陷进沙里，拔出来要用力",
        "沙面上有风纹，踩上去就平了",
    ],
    "rock": [
        "脚底硌得生疼",
        "石头是烫的，隔着鞋底也能感觉到",
        "一块松动的石头在脚下滑了一下",
        "岩石的棱角顶着脚心",
        "手扶了一下旁边的石壁，粗糙的",
        "碎石在脚底滚动，你稳住了",
    ],
    "snow": [
        "脚陷下去三寸，拔出来的时候有声音",
        "雪壳塌裂，碎冰钻进鞋帮",
        "新雪软，踩下去没到底",
        "雪被踩实了的地方滑",
        "脚踝周围的雪化了一点，湿了裤脚",
        "每一步都带出嘎吱一声",
    ],
    "forest": [
        "树根绊了一下，你没倒",
        "落叶踩上去沙沙响",
        "苔藓踩上去软的",
        "低枝刮过肩膀，留下一道湿痕",
        "脚下的腐叶层比想象的厚",
        "松针扎进鞋帮，拔了几根",
    ],
    "grass": [
        "露水打湿了鞋面",
        "草叶刮过小腿，留下一道湿痕",
        "草根抓着土，踩上去比想象的硬",
        "草穗扫过手背，痒的",
        "脚踩下去，草丛里有虫子跳开",
        "草地上有牛蹄印，踩进去刚好合脚",
    ],
    "urban": [
        "脚下的路面被磨得光滑",
        "地面是硬的，踩上去没有弹性",
        "路沿石的棱角硌了一下脚",
        "井盖踩上去咚的一声",
        "砖缝里长出来的草蹭过鞋底",
        "路面有一块翘起来，你跨过去了",
    ],
    "water_ocean": [
        "浪打在脚背上，凉的",
        "脚趾间的沙被吸走",
        "退浪拽着脚底的沙往海里去",
        "脚踩进水里，沙子塌了一圈",
        "浪花溅到膝盖，盐水黏在皮肤上",
        "贝壳碎片硌了一下脚心",
    ],
    "water_fresh": [
        "水凉得刺骨",
        "河底的石头滑，你差点摔倒",
        "水流推着小腿，要站稳得用力",
        "脚趾踩到一块圆石头，滑的",
        "水草从脚踝边溜过去",
        "溪水漫过脚背，凉意顺着腿往上走",
    ],
    "bare": [
        "碎石在脚下滑动",
        "地面干裂，踩上去碎了一层",
        "风把细沙吹进鞋里",
        "脚下的土硬得像石头",
        "一块干泥巴在脚底碎了",
        "碎石缝里有蚂蚁在搬家",
    ],
    "ice": [
        "脚底打滑，你重心往前倾",
        "冰面嘎吱响，像踩在玻璃上",
        "鞋底在冰上留不下印子",
        "冰面有一层薄水，踩上去溅开",
        "脚趾在冰上抠了一下才站住",
        "冰面裂了一条纹，没碎",
    ],
    "wetland": [
        "脚陷进泥里，拔出来咕的一声",
        "水草缠住了脚踝",
        "泥浆漫过鞋帮，袜子湿了",
        "脚踩下去，气泡从泥里冒出来",
        "湿地的泥黏，每一步都要拔脚",
        "脚边有青蛙跳进水里",
    ],
}

# Card 50: cold touch variants (used when cold > 5)
_COLD_TOUCH_VARIANTS: list[str] = [
    "手指是麻的，你搓了搓",
    "指尖碰了一下金属，粘住了似的",
    "手背的皮肤裂了一道口子",
    "你把手缩进袖子里，还是冷",
    "耳朵尖冻得发疼",
    "鼻尖是凉的，吸进去的气也是凉的",
    "你的手指弯起来费劲",
    "口袋里摸到什么，手已经没知觉了",
]

# ── River alignment text pool (Card 35: river rendering) ────────────
# 四种方向: 顺流(downstream)、逆流(upstream)、横渡(crossing)、沿河(along)
_RIVER_ALIGNMENT_TEXT: dict[str, list[str]] = {
    "downstream": [
        "水往下游走,你顺着它。",
        "河流的方向就是你的方向。水在脚边往低处去。",
        "顺着水流走。水知道路在哪。",
        "你跟着河走。水往低处去,你也是。",
        "河流往下游。你踩着岸边的石头,方向跟水一样。",
        "水声在下游的方向。你顺着河岸走。",
        "河往东去。你跟着它,脚下的泥是湿的。",
        "水流的方向,就是你要去的方向。你顺着走。",
    ],
    "upstream": [
        "你逆着水流走。每一步都要顶着水的脾气。",
        "河从上游来,你往上游去。水推着你的脚。",
        "逆流。水从你脚边冲过去,你不让它。",
        "你朝上游走。水流的方向跟你相反,你不在乎。",
        "河从上面来,你往上面走。水声一直在耳边。",
        "逆着河走。脚下的石头被水冲得圆。",
        "你逆流而上。水在脚踝边打转,你站住了。",
        "上游的方向。河从那边来,你往那边去。",
    ],
    "crossing": [
        "你踩着石头过河。水在脚踝以下。",
        "河不宽,你跨了三步就过去了。鞋底湿了。",
        "你淌水过河。水凉,石头滑,你一步一步地走。",
        "河在面前。你踩着露出水面的石头,一步一步跨过去。",
        "你涉水而过。水到膝盖,脚底的石头圆。",
        "河不深。你提着裤脚走过去,水凉得刺骨。",
        "你踩着河里的石头过河。每一步都得找稳的。",
        "过河。水从左边流过来,你从这边走到那边。",
    ],
    "along": [
        "你沿着河走。水声一直在左边。",
        "河在旁边。你跟它并排走,谁也不等谁。",
        "沿着河岸。水的声音从始至终都在。",
        "你走在河边。水面的光落在你脸上。",
        "河在右边。你沿着它走,脚下的路跟河一样长。",
        "沿着河走。水里的倒影跟着你走。",
        "你跟河平行。它走它的,你走你的。",
        "河岸上有路。你沿着走,水声一直在耳边。",
    ],
}


def _season(month: int, lat: float) -> str:
    """Get season name from month and latitude.

    Three-band tropical logic (card 74):
    - ITCZ <5°: both hemispheres wet Apr-Oct (ITCZ overhead)
    - Tropical 5-15°: May-Nov wet in north, Nov-May wet in south
    - Subtropical 15-23.5°: Jun-Oct wet in north, Dec-Mar wet in south
    - Temperate 23.5°+: standard spring/summer/autumn/winter
    """
    abs_lat = abs(lat)

    # Tropical bands: return wet/dry
    if abs_lat < 5:
        # ITCZ: both hemispheres wet when ITCZ overhead (Apr-Oct)
        return "wet" if 4 <= month <= 10 else "dry"
    elif abs_lat < 15:
        # Tropical: May-Nov wet in north, Nov-May wet in south
        if lat >= 0:
            return "wet" if 5 <= month <= 11 else "dry"
        else:
            return "wet" if month >= 11 or month <= 5 else "dry"
    elif abs_lat < 23.5:
        # Subtropical: Jun-Oct wet in north, Dec-Mar wet in south
        if lat >= 0:
            return "wet" if 6 <= month <= 10 else "dry"
        else:
            return "wet" if month >= 12 or month <= 3 else "dry"

    # Temperate: standard seasons with hemisphere flip
    if lat < 0:
        month = ((month - 1 + 6) % 12) + 1
    return ["winter", "winter", "spring", "spring", "spring", "summer",
            "summer", "summer", "autumn", "autumn", "autumn", "winter"][month - 1]


_SEASON_CONTEXT: dict[str, str] = {
    "spring": "春天。",
    "summer": "夏天。",
    "autumn": "秋天。",
    "winter": "冬天。",
    "wet": "雨季。",
    "dry": "旱季。",
}


# 时刻文案: 当地时间 → 中文时刻
_TIME_OF_DAY: dict[int, str] = {
    0: "深夜", 1: "深夜", 2: "深夜", 3: "深夜",
    4: "凌晨", 5: "凌晨",
    6: "清晨", 7: "清晨",
    8: "上午", 9: "上午", 10: "上午", 11: "上午",
    12: "正午",
    13: "下午", 14: "下午", 15: "下午", 16: "下午",
    17: "傍晚", 18: "傍晚",
    19: "黄昏", 20: "黄昏", 21: "黄昏",
    22: "深夜", 23: "深夜",
}

# 国家码 → 中文名(ISO 3166 常见全覆盖)
_COUNTRY_ZH: dict[str, str] = {
    "CN": "中国", "JP": "日本", "KR": "韩国", "KP": "朝鲜", "MN": "蒙古",
    "VN": "越南", "TH": "泰国", "MY": "马来西亚", "SG": "新加坡", "ID": "印度尼西亚",
    "PH": "菲律宾", "MM": "缅甸", "KH": "柬埔寨", "LA": "老挝", "BN": "文莱",
    "IN": "印度", "PK": "巴基斯坦", "NP": "尼泊尔", "BD": "孟加拉国", "LK": "斯里兰卡",
    "BT": "不丹", "MV": "马尔代夫", "AF": "阿富汗", "KZ": "哈萨克斯坦", "UZ": "乌兹别克斯坦",
    "TM": "土库曼斯坦", "KG": "吉尔吉斯斯坦", "TJ": "塔吉克斯坦",
    "IR": "伊朗", "IQ": "伊拉克", "TR": "土耳其", "SA": "沙特阿拉伯", "AE": "阿联酋",
    "IL": "以色列", "JO": "约旦", "LB": "黎巴嫩", "SY": "叙利亚", "YE": "也门",
    "OM": "阿曼", "QA": "卡塔尔", "KW": "科威特", "BH": "巴林", "GE": "格鲁吉亚",
    "AM": "亚美尼亚", "AZ": "阿塞拜疆", "EG": "埃及", "LY": "利比亚", "TN": "突尼斯",
    "DZ": "阿尔及利亚", "MA": "摩洛哥", "SD": "苏丹", "ET": "埃塞俄比亚", "KE": "肯尼亚",
    "TZ": "坦桑尼亚", "UG": "乌干达", "RW": "卢旺达", "NG": "尼日利亚", "GH": "加纳",
    "SN": "塞内加尔", "ML": "马里", "NE": "尼日尔", "TD": "乍得", "CM": "喀麦隆",
    "CD": "刚果(金)", "CG": "刚果(布)", "AO": "安哥拉", "ZM": "赞比亚", "ZW": "津巴布韦",
    "MZ": "莫桑比克", "MG": "马达加斯加", "ZA": "南非", "NA": "纳米比亚", "BW": "博茨瓦纳",
    "MU": "毛里求斯", "SC": "塞舌尔", "DJ": "吉布提", "SO": "索马里",
    "RU": "俄罗斯", "UA": "乌克兰", "BY": "白俄罗斯", "PL": "波兰", "CZ": "捷克",
    "SK": "斯洛伐克", "HU": "匈牙利", "RO": "罗马尼亚", "BG": "保加利亚", "RS": "塞尔维亚",
    "HR": "克罗地亚", "SI": "斯洛文尼亚", "BA": "波黑", "ME": "黑山", "MK": "北马其顿",
    "AL": "阿尔巴尼亚", "GR": "希腊", "IT": "意大利", "ES": "西班牙", "PT": "葡萄牙",
    "FR": "法国", "BE": "比利时", "NL": "荷兰", "LU": "卢森堡", "DE": "德国",
    "CH": "瑞士", "AT": "奥地利", "GB": "英国", "IE": "爱尔兰", "DK": "丹麦",
    "SE": "瑞典", "NO": "挪威", "FI": "芬兰", "IS": "冰岛", "FO": "法罗群岛",
    "EE": "爱沙尼亚", "LV": "拉脱维亚", "LT": "立陶宛", "MD": "摩尔多瓦",
    "US": "美国", "CA": "加拿大", "MX": "墨西哥", "GT": "危地马拉", "BZ": "伯利兹",
    "HN": "洪都拉斯", "SV": "萨尔瓦多", "NI": "尼加拉瓜", "CR": "哥斯达黎加", "PA": "巴拿马",
    "CU": "古巴", "JM": "牙买加", "HT": "海地", "DO": "多米尼加", "BS": "巴哈马",
    "BR": "巴西", "AR": "阿根廷", "CL": "智利", "PE": "秘鲁", "BO": "玻利维亚",
    "CO": "哥伦比亚", "VE": "委内瑞拉", "EC": "厄瓜多尔", "PY": "巴拉圭", "UY": "乌拉圭",
    "GY": "圭亚那", "SR": "苏里南",
    "AU": "澳大利亚", "NZ": "新西兰", "FJ": "斐济", "PG": "巴布亚新几内亚",
    "SB": "所罗门群岛", "VU": "瓦努阿图", "WS": "萨摩亚", "TO": "汤加", "GL": "格陵兰",
}

# 钩子模板: {dir} 是方位词,从数据来
_HOOKS_WATER: list[str] = [
    "水声在{dir}边,隐隐约约。",
    "{dir}边有浪的声音,顺着声音能走到水边。",
    "风从{dir}边来,带着水汽。",
    "{dir}边有水的味道,你还没看见水。",
    "你听见{dir}边有水声。不知道是河还是湖。",
    "{dir}方传来水的声音。你朝那边看了一眼。",
    "空气里的湿度告诉你,{dir}边有水。",
    "水声从{dir}边飘过来,隐隐的,断断续续。",
]
_HOOKS_UPHILL: list[str] = [
    "高处还有路,风从上面下来。",
    "往上走,山在上面等着。",
    "上面的风比底下冷,你还没上去就知道了。",
    "抬头看,路还在往上走。",
    "山在上面。你还没有到头。",
    "往上看,路绕过去了,你看不见那边有什么。",
    "高处有云的影子落在地上,你还没走到那里。",
    "上面的空气更薄,你还没到就已经喘了。",
]
_HOOKS_RADIO: list[str] = [
    "收音机的声音不知道从哪来,顺着它能找到有人烟的地方。",
    "哪个角落里漏出电台的声音,这里不荒凉。",
    "电台的声音从远处飘过来,断断续续的。",
    "有音乐声。你不知道从哪来,但你知道有人在。",
    "收音机的信号在风里飘。你顺着声音走。",
    "电台还在播。信号弱,但还在。",
    "远处有电台的声音。你朝那边看了一眼。",
    "收音机里有人说话。你听不清说什么,但声音在。",
]
_HOOKS_GENERIC: list[str] = [
    "再往前走,雾或者光,总有一个会变。",
    "路在脚下,还没走完。",
    "前面的路你看不见,但你还在走。",
    "风从前面吹过来,你不知道那边有什么。",
    "路没有尽头。你继续走。",
    "往前走,地平线还在远处。",
    "你不知道前面是什么,但你没有停。",
    "远处有什么在动。你看不清,但你朝那边走了一步。",
]


def _time_of_day(hour: int | None, phase: str = "day") -> str:
    """时刻词以太阳为准,不以钟点为准——极昼极夜钟点会说谎。"""
    if hour is None:
        return "此刻"
    if phase == "day" and (hour >= 21 or hour < 4):
        return "白夜"
    if phase == "night" and 9 <= hour < 16:
        return "极夜的正午"
    if phase in ("civil", "nautical") and (hour >= 22 or hour < 3):
        return "不落的黄昏"
    return _TIME_OF_DAY.get(hour, "深夜")


def _append_local_flavor(parts: list[str], place: str, rng: random.Random) -> None:
    """Try to append a local soundscape or taste entry for the given place.

    Called from render_establish to add local data (Bug 4 fix).
    Modifies parts in place. 40% chance for soundscape, 30% for taste.
    """
    if not place:
        return
    location_scenes = _load_location_scenes()
    # Try soundscape (40% chance)
    if rng.random() < 0.4:
        sound_fp = _SCENE_DIR / "scene_soundscape.txt"
        if sound_fp.exists():
            sound_pool = []
            for line in sound_fp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("[") and "] " in line:
                    bracket_end = line.index("] ")
                    bracket_place = line[1:bracket_end]
                    # Card 82: handle [地名|季] format
                    if "|" in bracket_place:
                        bracket_place = bracket_place.rsplit("|", 1)[0]
                    if bracket_place == place:
                        sound_pool.append(line[bracket_end + 2:])
            if sound_pool:
                parts.append(rng.choice(sound_pool))
                return
    # Try taste (30% chance)
    if rng.random() < 0.3:
        taste_fp = _SCENE_DIR / "scene_taste.txt"
        if taste_fp.exists():
            taste_pool = []
            for line in taste_fp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("[") and "] " in line:
                    bracket_end = line.index("] ")
                    bracket_place = line[1:bracket_end]
                    # Card 82: handle [地名|季] format
                    if "|" in bracket_place:
                        bracket_place = bracket_place.rsplit("|", 1)[0]
                    if bracket_place == place:
                        taste_pool.append(line[bracket_end + 2:])
            if taste_pool:
                parts.append(rng.choice(taste_pool))
                return


def render_establish(payload: dict, rng: random.Random) -> str:
    """开幕镜头。优先用场景文件,兜底用模板。"""
    place = payload.get("place", "未知之地")
    cc = payload.get("country_code")
    country = _COUNTRY_ZH.get(cc or "", "")
    phase = payload.get("phase", "day")
    moment = _time_of_day(payload.get("local_hour"), phase)
    surface = payload.get("surface", "grass")
    biome = payload.get("biome", "")
    weather = payload.get("weather") or {}
    precip = weather.get("precip", "none")

    # Season
    month = payload.get("month", 7)
    lat = payload.get("lat", 0.0)
    lon = payload.get("lon", 0.0)
    season_str = ""
    if month and lat is not None:
        season_str = _SEASON_CONTEXT.get(_season(month, lat), "")

    header = f"【{country + ',' if country else ''}{place},{moment}】"
    if season_str:
        header = f"【{country + ',' if country else ''}{place},{moment},{season_str}】"

    # ── Try scene files first ─────────────────────────────────────────
    elevation = payload.get("elevation", 0)

    # Build context for metadata filtering
    season = _season(month, lat) if month else "summer"
    if lat > 0:
        is_polar_day = (phase == "day" and abs(lat) > 60 and month and 4 <= month <= 8)
    else:
        is_polar_day = (phase == "day" and abs(lat) > 60 and month and (month >= 10 or month <= 2))
    ctx = {
        "season": season,
        "phase": phase,
        "wind_speed": weather.get("wind_ms", 0),
        "lat": lat,
        "temp": weather.get("temp_c"),
        "polar_day": is_polar_day,
        "biome": biome,
        "elevation": elevation,
        "features": set(),  # no waterfall/river data at establish time
    }

    scene_pool: list[str] = []
    scene_name = ""
    # Location-dependent RNG offset: ensures different places get different scenes
    _location_offset(rng, lat, lon)

    # ── Try location-specific scenes first (china/world enhanced, soundscape, taste) ──
    # Card 82: filter by season tags first, then fall back to word-based filter
    location_scenes = _load_location_scenes()
    if place in location_scenes:
        _loc_pool = location_scenes[place]
        # Card 82: exclude season-tagged entries that don't match current season
        _loc_seasonal = _get_location_seasonal()
        _loc_exclude: set[str] = set()
        for (_sp, _sn), _sdescs in _loc_seasonal.items():
            if _sp == place and _sn != season:
                _loc_exclude.update(_sdescs)
        if _loc_exclude:
            _loc_pool = [s for s in _loc_pool if s not in _loc_exclude]
        # Card 69: word-based winter filter as fallback for untagged entries
        if season in ("summer", "spring") and biome not in ("tundra", "glacier", "polar"):
            _winter_loc_words = ["下雪", "冰雪", "冰封", "冰面", "冰川", "冰冻", "冻土", "严寒", "积雪", "霜冻"]
            _loc_filtered = [s for s in _loc_pool if not any(w in s for w in _winter_loc_words)]
            if _loc_filtered:
                _loc_pool = _loc_filtered
            else:
                _loc_pool = []
        if _loc_pool:
            scene_text = rng.choice(_loc_pool)
            parts = [header, scene_text]
            # 附近地标
            nearby_places = payload.get("nearby_places", "")
            if nearby_places:
                parts.append(nearby_places)
            return "".join(parts)
        # Fall through to seasonal/generic rendering if all location scenes filtered

    if precip in _WEATHER_TO_SCENE:
        scene_name = _WEATHER_TO_SCENE[precip]
        # At high altitude, skip water/river scenes
        if elevation and elevation > 3000 and scene_name in ("water",):
            pass
        else:
            scene_pool = _load_scenes(scene_name)
    if not scene_pool and biome in _BIOME_TO_SCENE:
        scene_name = _BIOME_TO_SCENE[biome]
        # Biome guard: don't use water scenes at high altitude
        if elevation and elevation > 3000 and scene_name in ("water",):
            pass
        # Biome guard: city biome should only use urban scenes
        elif biome == "city" and scene_name != "urban":
            pass
        # Biome guard: mountain+rock should only use mountain scenes
        elif biome == "mountain" and surface == "rock" and scene_name not in ("mountains",):
            pass
        else:
            scene_pool = _load_scenes(scene_name)
    if not scene_pool and surface in _SURFACE_TO_SCENE:
        scene_name = _SURFACE_TO_SCENE[surface]
        # At high altitude, skip water/river scenes
        if elevation and elevation > 3000 and scene_name in ("water",):
            pass
        # Surface guard: water surfaces should only use water scenes
        elif surface in ("water_ocean", "water_fresh") and scene_name != "water":
            pass
        # Biome guard: coast/tundra should not get desert scenes
        elif biome in ("coast", "tundra") and scene_name == "deserts":
            pass
        else:
            scene_pool = _load_scenes(scene_name)

    # Time scene (always attempted, metadata-filtered)
    time_scene = ""
    moment_key = _MOMENT_TO_VISUAL.get(moment, "")
    if moment_key in _TIME_TO_SCENE:
        time_name = _TIME_TO_SCENE[moment_key]
        time_pool = _load_scenes(time_name)
        if time_pool:
            time_scene = _pick_scene(time_pool, time_name, rng, ctx)

    if scene_pool and scene_name:
        scene_text = _pick_scene(scene_pool, scene_name, rng, ctx)
        # 30% chance to use seasonal variant instead of generic
        if rng.random() < 0.3:
            seasonal_data = _load_seasonal()
            season_zh = _SEASON_EN_TO_ZH.get(season, "")
            # 1. Try exact place name match
            place_pool = seasonal_data.get((place, season_zh), [])
            # 2. Try biome-specific seasonal (built at build time, e.g. seasonal_coast.txt)
            if not place_pool and biome:
                biome_seasonal = _load_seasonal_biome(biome)
                place_pool = biome_seasonal.get((place, season_zh), [])
            # 3. Try biome-based match (standard seasons)
            if not place_pool and biome:
                biome_place = _BIOME_TO_SEASONAL_PLACE.get(biome, "")
                if biome_place:
                    place_pool = seasonal_data.get((biome_place, season_zh), [])
                    # Also try biome-specific file with biome place name
                    if not place_pool and biome:
                        biome_seasonal = _load_seasonal_biome(biome)
                        place_pool = biome_seasonal.get((biome_place, season_zh), [])
            # 4. Try tropical seasons for rainforest
            if not place_pool and biome == "rainforest":
                trop_season = _TROPICAL_SEASON.get(season, "")
                if trop_season:
                    place_pool = seasonal_data.get(("热带雨林", trop_season), [])
            if place_pool:
                scene_text = rng.choice(place_pool)
            else:
                # 5. Fall back to generic seasonal scene files
                seasonal_pool = _load_scenes(season)
                if seasonal_pool:
                    scene_text = rng.choice(seasonal_pool)
        parts = [header, scene_text]
        if time_scene and time_scene != scene_text:
            parts.append(time_scene)
        # 附近地标——单独加，不跟其他钩子竞争
        nearby_places = payload.get("nearby_places", "")
        if nearby_places:
            parts.append(nearby_places)
        # Bug 4: try to add local soundscape/taste for this place
        _append_local_flavor(parts, place, rng)
        return "".join(parts)

    # ── Fallback: template system ─────────────────────────────────────
    surface_zh = _SURFACE_ZH.get(surface, "大地")
    phase_key = _MOMENT_TO_VISUAL.get(moment, phase if phase in _ESTABLISH_VISUAL else "day")
    visual = rng.choice(_ESTABLISH_VISUAL[phase_key]).format(
        surface_zh=surface_zh,
        shape=payload.get("shape") or _SHAPE_BY_SURFACE.get(surface, "远处的一切"),
        light=payload.get("light", "星子"),
    )

    smell = ""
    precip = weather.get("precip", "none")
    if precip in _SMELL_BY_PRECIP:
        smell = _SMELL_BY_PRECIP[precip]
    elif payload.get("smell_hint"):
        smell = payload["smell_hint"]
    elif biome in _SMELL_BY_BIOME:
        smell_pool = _SMELL_BY_BIOME[biome]
        smell = rng.choice(smell_pool)
    elif surface in _SMELL_BY_SURFACE:
        smell = _SMELL_BY_SURFACE[surface]

    temp = weather.get("temp_c")
    temp_str = f"空气 {round(temp)} 度" if temp is not None else ""

    sound = payload.get("sound", "")

    # 钩子: 从 payload 给的数据钩子里挑一个
    hooks: list[str] = payload.get("hooks") or []
    hook = ""
    if hooks:
        hook_kind, hook_dir = rng.choice(hooks)
        pool = {
            "water": _HOOKS_WATER,
            "uphill": _HOOKS_UPHILL,
            "radio": _HOOKS_RADIO,
        }.get(hook_kind, _HOOKS_GENERIC)
        hook = rng.choice(pool).format(dir=hook_dir or "东")

    sections = [header, visual]
    if temp_str:
        sections.append(temp_str + ("," if smell else "。"))
    if smell:
        sections.append(smell)
    if sound:
        sections.append(sound)
    if hook:
        sections.append(hook)
    # 附近地标——单独加，不跟其他钩子竞争
    nearby_places = payload.get("nearby_places", "")
    if nearby_places:
        sections.append(nearby_places)
    # Bug 4: try to add local soundscape/taste for this place
    _append_local_flavor(sections, place, rng)
    return "".join(sections)


# ── module-level biome context for handlers that need it ─────────────
_CURRENT_BIOME: str = ""
_CURRENT_SEASON: str = ""
_CURRENT_LAT: float = 0.0
_RECENT_TOUCH: set[str] = set()
_RECENT_SCENES: list[str] = []
_SEG_GEOCODE_CACHE: dict[str, tuple[float, float] | None] = {}


# ── segment geocode / distance helpers ──────────────────────────────

def _geocode_segment(seg_name: str) -> tuple[float, float] | None:
    """Geocode a water-feature segment name, caching results."""
    if seg_name in _SEG_GEOCODE_CACHE:
        return _SEG_GEOCODE_CACHE[seg_name]
    stripped = seg_name.rstrip("段")
    hit = places.find(stripped)
    if hit is not None:
        result = (hit["lat"], hit["lon"])
    else:
        result = None
    _SEG_GEOCODE_CACHE[seg_name] = result
    return result


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Haversine distance in km between two (lat, lon) tuples."""
    import math
    R = 6371.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


# ── handler registry ─────────────────────────────────────────────────

def _render_humanities(payload: dict, prev: dict | None, rng: random.Random) -> str:
    """人文层卡: 事件/人物/作品。text 已经写好了,直接返回。"""
    return payload.get("text", "")


def _load_wf_scenes() -> dict:
    """Load water_features_scenes.json (per-river/lake scene data). Cached."""
    global _WF_SCENES_CACHE
    if _WF_SCENES_CACHE is None:
        fp = _SCENE_DIR / "water_features_scenes.json"
        if fp.exists():
            import json as _json
            _WF_SCENES_CACHE = _json.loads(fp.read_text(encoding="utf-8"))
        else:
            _WF_SCENES_CACHE = {}
    return _WF_SCENES_CACHE


def _render_water_features(payload: dict, prev: dict | None, rng: random.Random) -> str:
    """水文描写: 河流/湖泊/瀑布/溪流。

    Card 33: reads biome-specific product file (scene_water_{biome}.txt),
    then filters by structured card metadata (seasons, lat_band).
    Card 65: checks water_features_scenes.json for named water bodies first.
    """
    biome = _CURRENT_BIOME
    features = payload if isinstance(payload, list) else []

    # Build feature set from actual data
    feat_set = set()
    has_named = False
    named_water = ""
    for f in features:
        ftype = f.get("type", "") or ""
        fname = f.get("name", "") or ""
        if fname and fname != "无名水域":
            has_named = True
            if not named_water:
                named_water = fname
        if "瀑布" in ftype or "瀑布" in fname:
            feat_set.add("waterfall")
        if any(k in ftype for k in ("河", "溪", "江", "river")):
            feat_set.add("river")
        if "湖" in ftype or "湖" in fname or "lake" in ftype:
            feat_set.add("lake")
    if not feat_set:
        if has_named:
            feat_set.add("river")
        else:
            feat_set.add("lake")

    # Card 65: try per-water-body scenes first (water_features_scenes.json)
    wf_scenes = _load_wf_scenes()
    if named_water and wf_scenes:
        # Try exact match, then substring match
        entry = wf_scenes.get(named_water)
        if not entry:
            for key in wf_scenes:
                if len(key) >= 2 and (key in named_water or named_water in key):
                    entry = wf_scenes[key]
                    break
        if entry and isinstance(entry, dict):
            segments = entry.get("segments", {})
            if segments:
                # ── Card 75: nearest-segment selection ─────────────
                # Get feature coordinates from payload
                feat_coords = None
                for f in features:
                    flt = f.get("lat")
                    flon = f.get("lon")
                    if flt is not None and flon is not None:
                        feat_coords = (flt, flon)
                        break
                seg_name = None
                if feat_coords:
                    best_seg = None
                    best_d = float("inf")
                    for _sn in segments:
                        gc = _geocode_segment(_sn)
                        if gc is None:
                            continue
                        d = _haversine_km(feat_coords, gc)
                        if d < best_d:
                            best_d = d
                            best_seg = _sn
                    if best_seg is not None and best_d <= 100.0:
                        seg_name = best_seg
                if seg_name is None:
                    # Card 75: no coords → skip named scene, use generic biome pool
                    # (rng.choice would randomly pick across cities, causing cross-city bleed)
                    pass
                if seg_name is not None:
                    seg = segments[seg_name]
                    scene_text = seg.get("scene", "")
                    if scene_text:
                        return scene_text

    # Card 33: read biome-specific product file directly
    if biome:
        pool = _load_scenes(f"water_{biome}")
    else:
        pool = _load_scenes("water_features")  # fallback to legacy

    # Card 68: if biome-specific pool is empty, fall back to legacy
    # but filter out waterfall scenes for biomes that shouldn't have waterfalls
    if not pool and biome:
        pool = _load_scenes("water_features")
        # Exclude waterfall scenes from desert/grassland (inland, no waterfalls)
        if biome in ("desert", "grassland"):
            _WATERFALL_KEYWORDS = ["瀑布", "水帘", "彩虹", "水雾"]
            pool = [s for s in pool
                    if not any(k in s for k in _WATERFALL_KEYWORDS)]

    # Card 33: structured field filtering (replaces keyword blacklist)
    if pool and _CURRENT_SEASON:
        pool = filter_by_card_meta(pool, _CURRENT_SEASON, _CURRENT_LAT, biome)

    ctx = {"features": feat_set}
    if pool:
        return _pick_scene(pool, f"water_{biome}", rng, ctx)
    # Fallback: use the first feature's bearing
    if features:
        return f"{features[0].get('bearing', '东')}边有水。"
    return ""


_HANDLERS: dict[str, callable] = {
    "arrive": _render_arrive,
    "weather": _render_weather,
    "terrain": _render_terrain,
    "sky": _render_sky,
    "water": _render_water,
    "water_features": _render_water_features,
    "life": _render_life,
    "art": _render_art,
    "radio": _render_radio,
    "blocked": _render_blocked,
    "message": _render_message,
    "humanities": _render_humanities,
}
