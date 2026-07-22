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
import pathlib
import random
from typing import Sequence

# ── scene files (literary descriptions per biome/weather) ─────────────
_SCENE_DIR = pathlib.Path(__file__).resolve().parent / "data"
_SCENE_CACHE: dict[str, list[str]] = {}

# ── location-specific scene files ([地名] 描述 or 地名 描述) ──────────
_LOCATION_SCENES: dict[str, list[str]] | None = None


def _load_location_scenes() -> dict[str, list[str]]:
    """Load all location-specific scene files.

    Handles two formats:
      - [地名] 描述  (soundscape, taste, china_enhanced)
      - 地名 描述    (world_enhanced — no brackets)
    """
    global _LOCATION_SCENES
    if _LOCATION_SCENES is not None:
        return _LOCATION_SCENES

    _LOCATION_SCENES = {}
    for fname in ["scene_china_enhanced.txt", "scene_world_enhanced.txt",
                   "scene_soundscape.txt", "scene_taste.txt"]:
        fp = _SCENE_DIR / fname
        if not fp.exists():
            continue
        for line in fp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Bracket format: [地名] 描述
            if line.startswith("[") and "] " in line:
                bracket_end = line.index("] ")
                place = line[1:bracket_end]
                desc = line[bracket_end + 2:]
                _LOCATION_SCENES.setdefault(place, []).append(desc)
            # No-bracket format: 地名 描述 (world_enhanced)
            elif not line.startswith("["):
                sp = line.index(" ") if " " in line else -1
                if sp > 0:
                    place = line[:sp]
                    desc = line[sp + 1:]
                    _LOCATION_SCENES.setdefault(place, []).append(desc)
    return _LOCATION_SCENES


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

_SEASON_EN_TO_ZH: dict[str, str] = {
    "spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬",
}


def _load_seasonal() -> dict[tuple[str, str], list[str]]:
    """Parse seasonal.txt into {(place_or_biome, season_zh): [descriptions]}.

    File format: [城市名|季节] 描述
    """
    global _SEASONAL_CACHE
    if _SEASONAL_CACHE is not None:
        return _SEASONAL_CACHE

    import re
    _SEASONAL_CACHE = {}
    pattern = re.compile(r"\[([^|]+)\|([^\]]+)\]\s*(.+)")

    seasonal_fp = _SCENE_DIR / "seasonal.txt"
    if not seasonal_fp.exists():
        # Fallback: try old glob pattern
        for fp in _SCENE_DIR.glob("seasonal_*.txt"):
            for line in fp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = pattern.match(line)
                if m:
                    place, season, desc = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                    key = (place, season)
                    _SEASONAL_CACHE.setdefault(key, []).append(desc)
        return _SEASONAL_CACHE

    for line in seasonal_fp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = pattern.match(line)
        if m:
            place, season, desc = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
            key = (place, season)
            _SEASONAL_CACHE.setdefault(key, []).append(desc)

    return _SEASONAL_CACHE


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
    """Load scene variants from a scene_*.txt file, one variant per line."""
    if name not in _SCENE_CACHE:
        fp = _SCENE_DIR / f"scene_{name}.txt"
        if fp.exists():
            lines = [l.strip() for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()]
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
    """
    import hashlib
    h = hashlib.md5(f"{lat:.4f},{lon:.4f}".encode()).hexdigest()
    skip = int(h[:4], 16) % 7  # 0-6 extra random calls
    # Use seed if available (real Random), otherwise just consume values (mock)
    try:
        rng.seed(int(h[:8], 16))
    except (AttributeError, TypeError):
        pass
    for _ in range(skip):
        rng.random()


def _pick_scene(pool: list[str], name: str, rng: random.Random, ctx: dict) -> str:
    """Pick a scene from pool, filtering by metadata constraints."""
    meta = _load_meta().get(name, [])
    if meta and len(meta) == len(pool):
        valid = [t for t, m in zip(pool, meta) if _matches(m.get("requires", {}), ctx)]
        if valid:
            return rng.choice(valid)
        # All filtered out — return empty instead of bypassing constraints
        return ""
    # No metadata → pick from full pool (backward compatible)
    return rng.choice(pool)


# Map surface/biome to scene file name
_SURFACE_TO_SCENE: dict[str, str] = {
    "sand": "deserts", "bare": "deserts", "rock": "mountains",
    "snow": "snow", "ice": "snow",
    "forest": "forests", "grass": "grasslands",
    "water_ocean": "water", "water_fresh": "water",
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
    "mountain": "mountains", "island": "water", "coast": "water",
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
]

_WEATHER_ABS_VARIANTS: list[str] = [
    "天{text}着。{temp_c} 度,{feels_clause}风 {wind_ms} 米每秒{wind_tail}。",
    "{text}。{temp_c} 度,{feels_clause}风 {wind_ms} 米每秒{wind_tail}。",
    "此刻{text},{temp_c} 度。{feels_clause}风一阵一阵,{wind_ms} 米每秒{wind_tail}。",
]

_WEATHER_RAIN_VARIANTS: list[str] = [
    "雨正下。{temp_c} 度,风 {wind_ms} 米每秒。雨声把别的声音都盖住了。",
    "在下雨。{temp_c} 度,雨点砸在{surface_hint}上。风 {wind_ms} 米每秒。",
    "雨没有停的意思。{temp_c} 度,风 {wind_ms} 米每秒,世界只剩雨声。",
]

_WEATHER_SNOW_VARIANTS: list[str] = [
    "雪在下。{temp_c} 度。雪把声音都吃掉了。",
    "下着雪。{temp_c} 度,风 {wind_ms} 米每秒,雪斜着走。",
    "雪。{temp_c} 度,世界只剩白,和落雪的声音。",
]

_WEATHER_DELTA_VARIANTS: list[str] = [
    "{text}。{delta_desc}。现在 {temp_c} 度,风 {wind_ms} 米每秒。",
    "{delta_desc}。{text},{temp_c} 度,风 {wind_ms} 米每秒。",
    "天变了。{delta_desc}。此刻{text},{temp_c} 度,风 {wind_ms} 米每秒。",
]

_TERRAIN_VARIANTS: list[str] = [
    "脚下是{surface_desc}{slope_clause}。海拔 {elevation} 米{delta_clause}。",
    "{surface_desc}{slope_clause},就在脚下。海拔 {elevation} 米{delta_clause}。",
    "脚下的地是{surface_desc}{slope_clause}。{elevation} 米{delta_clause}。",
]

_TERRAIN_SCREE_VARIANTS: list[str] = [
    "脚下是{surface_desc}堆的坡,松的。每一步踩下去,都先滑半步,才吃住劲。海拔 {elevation} 米{delta_clause}。",
    "{surface_desc},松的。坡 {slope_deg} 度,每往上一步,都要付一点代价。海拔 {elevation} 米{delta_clause}。",
    "坡是{surface_desc}堆出来的,松的,{slope_deg} 度。走一步,滑半步。海拔 {elevation} 米{delta_clause}。",
]

_TERRAIN_FLAT_VARIANTS: list[str] = [
    "脚下是{surface_desc},平的,走起来不费力气。海拔 {elevation} 米{delta_clause}。",
    "地是{surface_desc},平的。海拔 {elevation} 米{delta_clause}。",
    "{surface_desc}铺开去,远处看不见头。海拔 {elevation} 米{delta_clause}。",
]

_TERRAIN_FLAT_GRASS_VARIANTS: list[str] = [
    "{surface_desc},一马平川。海拔 {elevation} 米{delta_clause}。",
    "草地平展展的,风一吹像水面。海拔 {elevation} 米{delta_clause}。",
    "{surface_desc}延伸到天际线。海拔 {elevation} 米{delta_clause}。",
]

# 裸地/沙地用这些
_TERRAIN_FLAT_BARE_VARIANTS: list[str] = [
    "{surface_desc}一眼望不到头,平的。海拔 {elevation} 米{delta_clause}。",
    "平的,但脚下的{surface_desc}每一块都不一样。海拔 {elevation} 米{delta_clause}。",
    "地是{surface_desc},平的,风把什么都吹走了。海拔 {elevation} 米{delta_clause}。",
]

_TERRAIN_FLAT_ROCK_VARIANTS: list[str] = [
    "{surface_desc}延伸到远处,平的。海拔 {elevation} 米{delta_clause}。",
    "碎石平铺,没有坡。海拔 {elevation} 米{delta_clause}。",
    "岩石平着铺开,风在上面走。海拔 {elevation} 米{delta_clause}。",
]

_TERRAIN_FLAT_URBAN_VARIANTS: list[str] = [
    "硬化路面延伸到远处,平的。海拔 {elevation} 米{delta_clause}。",
    "马路平的,车在跑。海拔 {elevation} 米{delta_clause}。",
    "路沿石被磨得发亮。海拔 {elevation} 米{delta_clause}。",
]

_TERRAIN_FLAT_WATER_VARIANTS: list[str] = [
    "水面平得像镜子。海拔 {elevation} 米{delta_clause}。",
    "{surface_desc},平的,没有一丝褶皱。海拔 {elevation} 米{delta_clause}。",
    "水平如镜。海拔 {elevation} 米{delta_clause}。",
]

_TERRAIN_HIGH_FLAT_VARIANTS: list[str] = [
    "地势平坦,但海拔 {elevation} 米,每一步都喘。脚下是{surface_desc}{delta_clause}。",
    "{surface_desc},平的。可 {elevation} 米的海拔压着胸口,走不快{delta_clause}。",
    "地是{surface_desc},没有坡。但 {elevation} 米的空气稀薄,喘得厉害{delta_clause}。",
]

_SKY_NIGHT_VARIANTS: list[str] = [
    "天黑了。{moon_str}{planet_str}{milky_str}{aurora_str}",
    "夜沉下来。{moon_str}{planet_str}{milky_str}{aurora_str}",
    "头顶是夜。{moon_str}{planet_str}{milky_str}{aurora_str}",
]

_SKY_DAY_VARIANTS: list[str] = [
    "太阳在 {sun_alt} 度,光落下来是直的。",
    "日头挂着,{sun_alt} 度。影子缩在脚边。",
    "白天。太阳 {sun_alt} 度,光从天顶附近砸下来。",
]

_SKY_DAY_LOW_VARIANTS: list[str] = [
    "太阳低着,{sun_alt} 度,影子拉得长。",
    "日头斜了,{sun_alt} 度。地上的影子比实物长。",
    "太阳快贴着地平线了,{sun_alt} 度,光是斜着来的。",
]

_WATER_COLD_VARIANTS: list[str] = [
    "海水 {sst} 度。脚踝先麻,然后是针扎。身体比人先记住这片海。",
    "水 {sst} 度。下去的第一秒,呼吸就乱了。",
    "海水 {sst} 度,冷得直接。脚趾先知道,然后是膝盖。",
]

_WATER_COOL_VARIANTS: list[str] = [
    "海水 {sst} 度。凉,一下一下贴在皮肤上。",
    "水 {sst} 度,凉意顺着脚踝往上爬。",
    "海水 {sst} 度。凉,但能忍,忍过十秒就是自己的了。",
]

_WATER_WARM_VARIANTS: list[str] = [
    "海水 {sst} 度。温的,像忘了凉下来。",
    "水 {sst} 度,温吞吞的,泡着不想动。",
    "海水 {sst} 度,暖的。海把人接住了。",
]

_LIFE_VARIANTS: list[str] = [
    "{time_desc},有人在离你 {distance_m} 的地方,遇见过{unit}{common_name}。此刻你不知道它在哪。",
    "{unit}{common_name}。{time_desc},{distance_m}外,有人见过它。它也许还在。",
    "{time_desc},{distance_m}之内,有人遇见过{common_name}。也许它正看着你——这不重要,知道它在,就够了。",
]

# Seasonal life encounter variants: (season) → list of templates
_LIFE_SEASONAL: dict[str, list[str]] = {
    "spring": [
        "{common_name}在繁殖,叫声急促,像在叫谁。{dist_str}外。",
        "春天,{common_name}从南方回来了。{dist_str}外,你听见了它的声音。",
        "{unit}{common_name}。{dist_str}外。空气里有花粉的味道。",
        "草地刚返青,{unit}{common_name}在上面走。{dist_str}外。",
    ],
    "summer": [
        "{unit}{common_name}在太阳底下活动。{dist_str}外,空气黏在皮肤上。",
        "热,{unit}{common_name}躲在阴凉里。{dist_str}外。",
        "蝉叫得整个林子都在响,{unit}{common_name}从你面前经过。{dist_str}外。",
        "{common_name}。夏天,{dist_str}外,它比你更适应这种热。",
    ],
    "autumn": [
        "{unit}{common_name}在忙着什么。秋天,{dist_str}外,空气凉了。",
        "落叶踩上去沙沙响,{unit}{common_name}在远处。{dist_str}。",
        "{common_name}在囤粮食,{dist_str}外。你知道冬天快来了。",
        "一群鸟往南飞,{unit}{common_name}没走。{dist_str}外。",
    ],
    "winter": [
        "远处有一串脚印,不是人的。你蹲下来看,是{common_name}的。{dist_str}外。",
        "冬天,{unit}{common_name}还在。{dist_str}外。你不知道它怎么过的冬。",
        "雪地上有{common_name}的爪印,新的。{dist_str}外,它刚走过。",
        "{unit}{common_name}。{dist_str}外。冷,但它比你更扛得住。",
    ],
}

# 合并视图: 测试要求每类 ≥3 变体
_WATER_VARIANTS: list[str] = _WATER_COLD_VARIANTS + _WATER_COOL_VARIANTS + _WATER_WARM_VARIANTS

_ART_VARIANTS: list[str] = [
    "此刻应景的一件：{artist}《{title}》。{intro}。{scene}",
    "有一件作品在等你——{artist}《{title}》。{intro}。{scene}",
    "{artist}《{title}》。{intro}。{scene}",
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
]

_BLOCKED_VARIANTS: list[str] = [
    "前面是{reason}。走不通,得绕。",
    "{reason}挡在前面。此路不通,换个方向。",
    "路到头了——{reason}。山不让步,人绕。",
]

_MESSAGE_VARIANTS: list[str] = [
    "有人在你之前走过这里。他留了一句:「{content}」",
    "路上躺着一句留言:「{content}」——不知道是谁,也不知道是什么时候。",
    "前人经过这里,留下一句:「{content}」",
    "你不是第一个到这的人。有人说:「{content}」",
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


def _temp_delta_line(old_temp: float, new_temp: float) -> str:
    diff = round(new_temp - old_temp)
    if diff > 0:
        return f"气温升了 {diff} 度"
    if diff < 0:
        return f"气温降了 {abs(diff)} 度"
    return "气温没变"


def _wind_delta_line(old_wind: float, new_wind: float) -> str:
    diff = round(new_wind - old_wind)
    if abs(diff) < 2:
        return ""
    if diff > 0:
        return f"风从 {round(old_wind)} 米每秒长到 {round(new_wind)} 米每秒"
    return f"风从 {round(old_wind)} 米每秒落到 {round(new_wind)} 米每秒"


def _feels_clause(feels_c: float, temp_c: float) -> str:
    """体感差异的物理说法。返回带尾逗号或空串。"""
    diff = round(feels_c - temp_c)
    if diff > 3:
        return f"湿气把体感往上抬了 {diff} 度,"
    if diff < -3:
        return f"风把体感往下压了 {abs(diff)} 度,"
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
) -> str:
    """渲染一种感官。优先用场景文件,兜底用模板。kind 见 _HANDLERS。"""
    # Try scene files for terrain/weather/water
    # Inject biome/elevation into payload for scene selection guards
    if isinstance(payload, dict):
        scene_payload = {**payload, "biome": biome or payload.get("biome", ""), "elevation": elevation or payload.get("elevation", 0)}
    else:
        scene_payload = {"biome": biome, "elevation": elevation}
    scene = _scene_for_kind(kind, scene_payload, rng,
                            lat=scene_payload.get("lat", 0.0),
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


def compose(sections: list[str], rng: random.Random) -> str:
    """把渲染好的段落拼成一份身体报告。段落间给过渡,但不抢戏。"""
    sections = [s for s in sections if s and s.strip()]
    if not sections:
        return ""

    transitions = ["", "同时,", "头顶上,", "风里,", "远处,", "走着走着,"]

    parts: list[str] = []
    for i, s in enumerate(sections):
        if i == 0:
            parts.append(s)
        else:
            t = _pick(transitions, rng)
            parts.append(t + s)

    return "".join(parts)


def sanity_check(text: str, env: dict) -> str:
    """Last-resort consistency check: fix obvious data-prose contradictions.

    Returns the (possibly patched) text. This is the final safety net,
    not the primary filtering mechanism — scene metadata handles that.
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

    # Storm: remove calm bird descriptions
    if wind >= 15:
        for bird in ("海鸥蹲", "鸟蹲", "鸽子蹲", "停在桩"):
            if bird in text:
                text = text.replace(bird, "风里有鸟")

    # Night: remove sun references (unless it's about sunset)
    if phase == "night":
        if "太阳" in text and "落" not in text and "没" not in text:
            text = text.replace("太阳", "月亮")

    # Summer: remove frozen/ice references
    if season in ("summer", "spring"):
        for ice in ("冻住了", "冰面", "冰冻", "结冰"):
            if ice in text:
                text = text.replace(ice, "水面")

    return text


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
    feels_clause = _feels_clause(feels_c, payload["temp_c"])

    # 物理外推: 风速超过步行速度(~1.1m/s)的八倍且有云,云比人快
    cloudy = any(w in text for w in ("云", "阴"))
    wind_tail = "。这样的风里,云走得比人快" if (wind_ms >= 9 and cloudy) else ""

    prev_weather = (prev or {}).get("weather")
    if prev_weather is not None:
        old_temp = prev_weather.get("temp_c", payload["temp_c"])
        old_wind = prev_weather.get("wind_ms", payload["wind_ms"])
        delta_desc = _temp_delta_line(old_temp, payload["temp_c"])
        wind_line = _wind_delta_line(old_wind, payload["wind_ms"])
        if wind_line:
            delta_desc += "," + wind_line
        tmpl = _pick(_WEATHER_DELTA_VARIANTS, rng)
        return tmpl.format(temp_c=temp_c, wind_ms=wind_ms, text=text, delta_desc=delta_desc)

    if precip == "rain":
        tmpl = _pick(_WEATHER_RAIN_VARIANTS, rng)
        return tmpl.format(temp_c=temp_c, wind_ms=wind_ms, surface_hint="地")
    if precip == "snow":
        tmpl = _pick(_WEATHER_SNOW_VARIANTS, rng)
        return tmpl.format(temp_c=temp_c, wind_ms=wind_ms)

    tmpl = _pick(_WEATHER_ABS_VARIANTS, rng)
    return tmpl.format(
        temp_c=temp_c,
        wind_ms=wind_ms,
        text=text or "晴",
        feels_clause=feels_clause,
        wind_tail=wind_tail,
    )


def _render_terrain(payload: dict, prev: dict | None, rng: random.Random) -> str:
    surface_key = payload.get("surface", "rock")
    slope_deg = payload.get("slope_deg", 0)
    elevation = round(payload.get("elevation", 0))
    elevation_delta = payload.get("elevation_delta", 0)
    biome = payload.get("biome", "")

    surface_desc = _SURFACE_DESC.get(surface_key, surface_key)

    if elevation_delta > 0:
        delta_clause = f",又抬高了 {round(elevation_delta)} 米"
    elif elevation_delta < 0:
        delta_clause = f",又落下了 {abs(round(elevation_delta))} 米"
    else:
        delta_clause = ""

    result = ""

    if surface_key in ("rock", "bare") and slope_deg > 15:
        tmpl = _pick(_TERRAIN_SCREE_VARIANTS, rng)
        result = tmpl.format(
            surface_desc=surface_desc,
            slope_deg=round(slope_deg),
            elevation=elevation,
            delta_clause=delta_clause,
        )
    elif slope_deg < 1.0:
        if elevation > 2500:
            tmpl = _pick(_TERRAIN_HIGH_FLAT_VARIANTS, rng)
        elif surface_key in ("bare", "sand"):
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
            elevation=elevation,
            delta_clause=delta_clause,
        )
    else:
        slope_clause = f",坡 {round(slope_deg)} 度"
        tmpl = _pick(_TERRAIN_VARIANTS, rng)
        result = tmpl.format(
            surface_desc=surface_desc,
            slope_clause=slope_clause,
            elevation=elevation,
            delta_clause=delta_clause,
        )

    # Append touch description
    touch_pool = _TOUCH_BY_SURFACE.get(surface_key, [])
    if touch_pool:
        result += rng.choice(touch_pool) + "。"

    # Append smell description
    smell_pool = _SMELL_BY_BIOME.get(biome, _SMELL_BY_BIOME.get(surface_key, []))
    if smell_pool:
        result += rng.choice(smell_pool) + "。"

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

        tmpl = _pick(_SKY_NIGHT_VARIANTS, rng)
        return tmpl.format(moon_str=moon_str, planet_str=planet_str, milky_str=milky_str, aurora_str=aurora_str)

    sun_alt_r = round(sun_alt)
    if sun_alt_r < 15:
        tmpl = _pick(_SKY_DAY_LOW_VARIANTS, rng)
    else:
        tmpl = _pick(_SKY_DAY_VARIANTS, rng)
    return tmpl.format(sun_alt=sun_alt_r)


def _render_water(payload: dict, prev: dict | None, rng: random.Random) -> str:
    sst = round(payload.get("sea_surface_temp", payload.get("sst", 20)))
    if sst < 10:
        tmpl = _pick(_WATER_COLD_VARIANTS, rng)
    elif sst < 22:
        tmpl = _pick(_WATER_COOL_VARIANTS, rng)
    else:
        tmpl = _pick(_WATER_WARM_VARIANTS, rng)
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
    "此刻看它，比任何时候都合适。",
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
}


def _genre_zh(genre: str) -> str:
    """电台流派标签中译,查不到的原样保留。"""
    if not genre:
        return "音乐"
    parts = [g.strip() for g in genre.split(",") if g.strip()]
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
    ],
    "civil": [
        "天边的光斜过来,{surface_zh}的影子都拉长了。",
        "橘红色的天边,{shape}成了剪影。",
    ],
    "night": [
        "天黑了,{surface_zh}沉进夜色里,只有{light}还亮着。",
        "夜把{surface_zh}收走了,{light}是仅剩的坐标。",
    ],
    "dawn": [
        "天边刚撕开一条缝,光先落在{surface_zh}的尖上。",
        "晨雾还没散,{shape}在雾里浮着。",
    ],
}

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
    "rainforest": ["腐叶的甜味混着泥土的腥", "空气黏在皮肤上，带着花香和霉味", "潮湿的木头味道"],
    "desert": ["干燥的热气，没有味道但你闻到了'干'", "沙子被太阳烤过的味道", "远处有植物烧焦的味道"],
    "tundra": ["冷空气，干净的，带一点金属", "苔藓的味道，湿的，像刚下过雪", "冻土化开的泥腥味"],
    "mountain": ["稀薄的空气，闻起来什么都没有", "岩石被太阳晒热的味道", "远处有雪的味道，冷的"],
    "coast": ["海盐混着海藻的腥", "鱼的味道，淡的，被风吹散了", "潮湿的木头，码头的味道"],
    "city": ["油烟和香料的味道", "汽车尾气混着烤面包的味道", "街角飘来咖啡和烤面包的香气"],
    "grassland": ["青草碾碎的味道", "干草的味道，暖的", "远处有篝火的烟味"],
    "volcano": ["硫磺的味道，刺鼻", "热石头的味道，像铁", "蒸汽带着矿物质的涩"],
    "wetland": ["腐殖质的味道，浓的", "水草的腥味", "泥巴的味道，潮的"],
    "snow": ["冷空气，干净得发苦", "雪化成水的味道，带一点泥土", "风里什么都没有，但你知道那是雪"],
}

_TOUCH_BY_SURFACE: dict[str, list[str]] = {
    "sand": ["脚踩下去，沙子陷了半寸", "沙子从脚趾缝里挤出来"],
    "rock": ["脚底硌得生疼", "石头是烫的，隔着鞋底也能感觉到"],
    "snow": ["脚陷下去三寸，拔出来的时候有声音", "雪壳塌裂，碎冰钻进鞋帮"],
    "forest": ["树根绊了一下，你没倒", "落叶踩上去沙沙响"],
    "grass": ["露水打湿了鞋面", "草叶刮过小腿，留下一道湿痕"],
    "urban": ["人行道的砖缝里长了草", "水泥地硬得像铁"],
    "water_ocean": ["浪打在脚背上，凉的", "脚趾间的沙被吸走"],
    "water_fresh": ["水凉得刺骨", "河底的石头滑，你差点摔倒"],
}


def _season(month: int, lat: float) -> str:
    """Get season name from month and latitude. Northern hemisphere default, southern flipped."""
    if lat < 0:
        month = (month + 6) % 12
    return ["winter", "winter", "spring", "spring", "spring", "summer",
            "summer", "summer", "autumn", "autumn", "autumn", "winter"][month - 1]


_SEASON_CONTEXT: dict[str, str] = {
    "spring": "春天。",
    "summer": "夏天。",
    "autumn": "秋天。",
    "winter": "冬天。",
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
]
_HOOKS_UPHILL: list[str] = [
    "高处还有路,风从上面下来。",
    "往上走,山在上面等着。",
]
_HOOKS_RADIO: list[str] = [
    "收音机的声音不知道从哪来,顺着它能找到有人烟的地方。",
    "哪个角落里漏出电台的声音,这里不荒凉。",
]
_HOOKS_GENERIC: list[str] = [
    "再往前走,雾或者光,总有一个会变。",
    "路在脚下,还没走完。",
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
                    if line[1:bracket_end] == place:
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
                    if line[1:bracket_end] == place:
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
    is_polar_day = (phase == "day" and abs(lat) > 60 and month and 4 <= month <= 8)
    ctx = {
        "season": season,
        "phase": phase,
        "wind_speed": weather.get("wind_ms", 0),
        "lat": lat,
        "temp": weather.get("temp_c"),
        "polar_day": is_polar_day,
        "features": set(),  # no waterfall/river data at establish time
    }

    scene_pool: list[str] = []
    scene_name = ""
    # Location-dependent RNG offset: ensures different places get different scenes
    _location_offset(rng, lat, lon)

    # ── Try location-specific scenes first (china/world enhanced, soundscape, taste) ──
    location_scenes = _load_location_scenes()
    if place in location_scenes:
        # Use location scene with 60% probability; otherwise fall through to generic
        if rng.random() < 0.6:
            scene_text = rng.choice(location_scenes[place])
            parts = [header, scene_text]
            # Try to add a soundscape overlay (40% chance)
            if rng.random() < 0.4:
                # Filter to soundscape-only entries for this place
                soundscape_fp = _SCENE_DIR / "scene_soundscape.txt"
                if soundscape_fp.exists():
                    sound_pool = []
                    for line in soundscape_fp.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line.startswith("[") and "] " in line:
                            bracket_end = line.index("] ")
                            if line[1:bracket_end] == place:
                                sound_pool.append(line[bracket_end + 2:])
                    if sound_pool:
                        parts.append(rng.choice(sound_pool))
            # 附近地标
            nearby_places = payload.get("nearby_places", "")
            if nearby_places:
                parts.append(nearby_places)
            return "".join(parts)

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
            # 2. Try biome-based match (standard seasons)
            if not place_pool and biome:
                biome_place = _BIOME_TO_SEASONAL_PLACE.get(biome, "")
                if biome_place:
                    place_pool = seasonal_data.get((biome_place, season_zh), [])
            # 3. Try tropical seasons for rainforest
            if not place_pool and biome == "rainforest":
                trop_season = _TROPICAL_SEASON.get(season, "")
                if trop_season:
                    place_pool = seasonal_data.get(("热带雨林", trop_season), [])
            if place_pool:
                scene_text = rng.choice(place_pool)
            else:
                # 3. Fall back to generic seasonal scene files
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


# ── handler registry ─────────────────────────────────────────────────

def _render_humanities(payload: dict, prev: dict | None, rng: random.Random) -> str:
    """人文层卡: 事件/人物/作品。text 已经写好了,直接返回。"""
    return payload.get("text", "")


def _render_water_features(payload: dict, prev: dict | None, rng: random.Random) -> str:
    """水文描写: 河流/湖泊/瀑布/溪流。从 scene_water_features.txt 取场景。"""
    pool = _load_scenes("water_features")
    features = payload if isinstance(payload, list) else []
    # Build feature set from actual data
    feat_set = set()
    has_named = False
    for f in features:
        ftype = f.get("type", "") or ""
        fname = f.get("name", "") or ""
        if fname and fname != "无名水域":
            has_named = True
        if "瀑布" in ftype or "瀑布" in fname:
            feat_set.add("waterfall")
        if any(k in ftype for k in ("河", "溪", "江", "river")):
            feat_set.add("river")
        if "湖" in ftype or "湖" in fname or "lake" in ftype:
            feat_set.add("lake")
    # Only use waterfall scenes if there's actually a waterfall
    # For unnamed lakes/ponds, use lake scenes
    if not feat_set:
        if has_named:
            feat_set.add("river")  # named water feature, assume flowing
        else:
            feat_set.add("lake")   # unnamed ponds/lakes

    # Biome filtering: exclude scenes inappropriate for the biome
    biome = _CURRENT_BIOME
    if pool and biome:
        # scene_water_features.txt line indices (0-based):
        # 0: stream, 1: lake, 2: river, 3: frozen river, 4: braided river,
        # 5: creek, 6: frozen lake, 7: waterfall, 8: river beach, 9: well
        _LAKE_IDX = {1, 6}       # lake, frozen lake

        if biome in ("tundra", "desert"):
            # No lakes in tundra/desert; use only non-lake scenes
            exclude = _LAKE_IDX
            filtered = [s for i, s in enumerate(pool) if i not in exclude]
            if filtered:
                pool = filtered
        elif biome == "coast":
            # Coastal locations: no inland lake scenes
            exclude = _LAKE_IDX
            filtered = [s for i, s in enumerate(pool) if i not in exclude]
            if filtered:
                pool = filtered

    ctx = {"features": feat_set}
    if pool:
        return _pick_scene(pool, "water_features", rng, ctx)
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
