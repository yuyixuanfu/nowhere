"""Sea surface temperature with two-tier fallback: Open-Meteo marine -> climate zone.

Returns None for land points (determined by terrain.is_water).
"""

from __future__ import annotations

import datetime
import hashlib
import math
import random
from typing import Final

from nowhere import providers, terrain

# ── Climate zone SST tables ────────────────────────────────────────
# zone -> [jan, feb, ..., dec] sea surface temp in celsius
_SST_CLIMATE: Final[dict[str, list[float]]] = {
    "equator":      [28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
    "subtropical":  [20, 19, 19, 20, 22, 24, 26, 27, 26, 24, 22, 20],
    "temperate":    [8,  7,  7,  9, 12, 15, 18, 20, 19, 16, 12,  9],
    "subarctic":    [2,  1,  1,  2,  4,  7, 10, 12, 11,  8,  5,  3],
    "polar":       [-1, -1, -1,  0,  1,  2,  3,  3,  2,  1,  0, -1],
}


def _climate_zone(lat: float) -> str:
    """Map latitude to a climate zone name."""
    abs_lat = abs(lat)
    if abs_lat < 10:
        return "equator"
    if abs_lat < 30:
        return "subtropical"
    if abs_lat < 55:
        return "temperate"
    if abs_lat < 70:
        return "subarctic"
    return "polar"


def _stable_random(lat: float, lon: float, low: float, high: float) -> float:
    """Return a deterministic pseudo-random float seeded by lat/lon."""
    seed_str = f"{lat:.2f},{lon:.2f}"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed).uniform(low, high)


async def sea_surface_temp(lat: float, lon: float) -> float | None:
    """Return sea surface temperature in Celsius, or None for land.

    Fallback chain: Open-Meteo marine -> climate zone offline table.
    """
    # ── Land check ──────────────────────────────────────────────────
    if not terrain.is_water(lat, lon):
        return None

    # ── Online: Open-Meteo marine ───────────────────────────────────
    url = (
        f"https://marine-api.open-meteo.com/v1/marine"
        f"?latitude={lat}&longitude={lon}"
        f"&current=sea_surface_temperature"
    )
    data = await providers.fetch_json(url, source="openmeteo-marine", cache_ttl=600)
    if data is not None:
        cur = data.get("current")
        if cur and "sea_surface_temperature" in cur:
            try:
                return float(cur["sea_surface_temperature"])
            except (ValueError, TypeError):
                pass

    # ── Offline fallback: climate zone table ────────────────────────
    zone = _climate_zone(lat)
    month = datetime.date.today().month - 1  # 0-indexed
    # Southern hemisphere: shift month by 6 to flip seasons
    if lat < 0:
        month = (month + 6) % 12
    base_temp = _SST_CLIMATE[zone][month]
    jitter = _stable_random(lat, lon, -1.5, 1.5)
    return round(base_temp + jitter, 1)


# ── SST description ──────────────────────────────────────────────────

_SST_TEMPLATES: dict[str, list[str]] = {
    "freezing": [
        "海水 {sst} 度。你把手指伸进去,骨头都凉了。拔出来的时候指尖是白的。",
        "水 {sst} 度。你蹲在岸边,手伸进去一秒就缩回来。这水不是给活人泡的。",
    ],
    "cold": [
        "海水 {sst} 度。凉的,但不是不能忍。你把脚伸进去,脚趾很快就麻了。",
        "水 {sst} 度。你蹚了几步,水没过膝盖。腿上起了一层鸡皮疙瘩。凉意从脚底往上走。",
    ],
    "cool": [
        "海水 {sst} 度。温的,像忘了凉下来。你把脚泡在里面,不想拔出来。",
        "水 {sst} 度。泡着舒服,但不能泡太久。你感觉身体在慢慢变凉。",
    ],
    "warm": [
        "海水 {sst} 度。暖的,像洗澡水。你往里走了几步,水没过腰。你不想出来。",
        "水 {sst} 度,温吞吞的。你躺在浅水里,浪一下一下推你。你闭上眼睛。",
    ],
    "hot": [
        "海水 {sst} 度。热的,像泡温泉。你泡在里面,汗从额头上渗出来。",
        "水 {sst} 度。你怀疑这是不是海——比体温还高。你浮在水面上,什么都不想。",
    ],
}


def _sst_zone(sst: float) -> str:
    if sst < 5:
        return "freezing"
    if sst < 12:
        return "cold"
    if sst < 20:
        return "cool"
    if sst < 28:
        return "warm"
    return "hot"


def describe_sst(sst: float, rng: random.Random) -> str:
    """Generate literary description of sea surface temperature."""
    zone = _sst_zone(sst)
    tmpl = rng.choice(_SST_TEMPLATES[zone])
    return tmpl.format(sst=round(sst))


# ── Ocean current description ────────────────────────────────────────

def describe_current(lat: float, lon: float, sst: float, rng: random.Random) -> str:
    """Generate a brief ocean current description based on SST and latitude."""
    zone = _climate_zone(lat)
    # Warm currents flow poleward (higher SST than expected for latitude)
    # Cold currents flow equatorward (lower SST than expected)
    expected = _SST_CLIMATE[zone][datetime.date.today().month - 1]
    if lat < 0:
        expected = _SST_CLIMATE[zone][(datetime.date.today().month + 5) % 12]
    diff = sst - expected

    if diff > 3:
        templates = [
            "这带水暖,有暖流经过。鱼群喜欢跟着暖流走。",
            "暖流把热带的水推到这里。海面比同纬度的暖。",
        ]
    elif diff < -3:
        templates = [
            "这带水冷,有寒流从高纬度涌过来。雾经常从冷水面升起来。",
            "寒流经过,水比同纬度的凉。海面上有一层薄雾。",
        ]
    else:
        templates = [
            "洋流平稳,水温正常。海面没有异常。",
        ]
    return rng.choice(templates)


# ── Marine life encounters ───────────────────────────────────────────

_MARINE_SCENES: dict[str, list[str]] = {
    "fish": [
        "你看见水下有银色的影子在动。一群鱼,不知道什么品种。它们转了个弯,鳞片反了一下光。",
        "水面突然炸开。一条鱼跳出来,又落回去。你只看见了水花和一道银光。",
        "你在浅水里看见一条鱼。它停在水底不动,你在岸上不动。你们对视了几秒,它摆尾走了。",
    ],
    "mollusk": [
        "沙滩上有一只水母,透明的,像一团凝固的水。你蹲下来看,伞盖边缘卷着。它还活着。",
        "礁石上长满了贝壳。你用指甲抠了一个下来,里面是空的。壳的内壁是彩虹色的。",
        "你捡到一个海螺。你把耳朵贴上去,听见了海的声音。你知道那是空气共振,但你选择相信。",
    ],
    "crab": [
        "退潮了,礁石缝里有小螃蟹。你伸手去抓,它比你快。你试了三次,终于抓到了一只。壳是蓝的。",
        "沙滩上有螃蟹的洞。你蹲在旁边等,等了五分钟,一只小钳子伸出来了。你动了一下,它又缩回去了。",
    ],
    "coral": [
        "水里有水母。透明的,像一朵花在水里开。你用棍子碰了一下,它缩了一下,又张开了。",
        "你看见珊瑚。颜色比你想象的淡——不是照片里那种亮色。但它活着,在水流里轻轻动。",
    ],
    "turtle": [
        "你看见一只海龟。它在水面上浮着,头伸出来看了一眼你,又缩回去了。它比你老。",
        "礁石上有一只海蜥蜴。它晒着太阳,一动不动。你走近了,它歪了一下头,没跑。",
    ],
    "whale": [
        "远处的水面突然隆起来。你看了三秒,才反应过来是鲸鱼。它喷了一口气,水柱在阳光里散开了。",
        "岸边有海豹。它趴在石头上,胖得像个袋子。你走近了十步,它看了你一眼,没动。又近了五步,它哼了一声,滚进了水里。",
        "你看见海豚。三只,在浪里跳。它们排成一排,同时跃起来,又同时落下去。像排练过的。",
    ],
}

# Latitude-based marine life pools (offline fallback)
_MARINE_BY_LAT: dict[str, list[tuple[str, list[str]]]] = {
    "tropical": [
        ("热带鱼", _MARINE_SCENES["fish"]),
        ("珊瑚", _MARINE_SCENES["coral"]),
        ("海龟", _MARINE_SCENES["turtle"]),
        ("海螺", _MARINE_SCENES["mollusk"]),
    ],
    "temperate": [
        ("鱼群", _MARINE_SCENES["fish"]),
        ("螃蟹", _MARINE_SCENES["crab"]),
        ("水母", _MARINE_SCENES["mollusk"]),
        ("海豹", _MARINE_SCENES["whale"]),
    ],
    "polar": [
        ("鲸鱼", _MARINE_SCENES["whale"]),
        ("海豹", _MARINE_SCENES["whale"]),
        ("海豚", _MARINE_SCENES["whale"]),
    ],
}


def _marine_zone(lat: float) -> str:
    abs_lat = abs(lat)
    if abs_lat < 25:
        return "tropical"
    if abs_lat < 60:
        return "temperate"
    return "polar"


def _offline_marine(lat: float, rng: random.Random) -> dict:
    """Generate a marine life encounter without API."""
    zone = _marine_zone(lat)
    pool = _MARINE_BY_LAT[zone]
    name, scenes = rng.choice(pool)
    scene = rng.choice(scenes)
    dist = rng.randint(50, 2000)
    return {"common_name": name, "distance_m": dist, "scene": scene}


async def marine_life(lat: float, lon: float, rng: random.Random) -> dict | None:
    """Find a nearby marine wildlife observation.

    Tries iNaturalist API first, falls back to offline latitude-based scenes.
    Returns {"common_name", "distance_m", "scene"} or None.
    """
    # Only for water points
    if not terrain.is_water(lat, lon):
        return None

    # Try online first
    try:
        params = (
            "lat={}&lng={}&radius={}&per_page=20"
            "&order=desc&order_by=observed_on&locale=zh-CN"
            "&iconic_taxa=Actinopterygii,Mollusca,Crustacea,Cnidaria,Reptilia,Mammalia"
        ).format(lat, lon, 15)
        url = f"https://api.inaturalist.org/v1/observations?{params}"
        data = await providers.fetch_json(url, source="inaturalist", cache_ttl=300, timeout=8.0)
        if data and data.get("results"):
            obs = rng.choice(data["results"])
            taxon = obs.get("taxon", {})
            common_name = taxon.get("preferred_common_name") or taxon.get("name", "")
            if not any("一" <= ch <= "鿿" for ch in common_name):
                common_name = taxon.get("name", "")
            if common_name:
                geo = obs.get("geojson", {})
                coords = geo.get("coordinates", [])
                if len(coords) >= 2:
                    dist = _haversine_km(lat, lon, coords[1], coords[0])
                    dist = max(10, min(int(dist * 1000), 15000))
                else:
                    dist = rng.randint(50, 500)
                # Pick scene by taxon
                taxon_lower = taxon.get("name", "").lower()
                scene_key = "fish"
                for key in ("mollusk", "crab", "coral", "turtle", "whale"):
                    if key in taxon_lower:
                        scene_key = key
                        break
                scene = rng.choice(_MARINE_SCENES.get(scene_key, _MARINE_SCENES["fish"]))
                return {"common_name": common_name, "distance_m": dist, "scene": scene}
    except Exception:
        pass

    # Offline fallback
    return _offline_marine(lat, rng)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    a = min(a, 1.0)
    return 2 * 6371.0 * math.asin(math.sqrt(a))
