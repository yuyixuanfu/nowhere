"""Life encounters -- nearby wildlife via iNaturalist."""

from __future__ import annotations

import math
import random
from urllib.parse import urlencode

from nowhere import providers

SOURCE = "inaturalist"

_NOCTURNAL_KEYWORDS = ("owl", "bat", "moth", "nightjar", "opossum", "raccoon", "firefly")
_AMPHIBIAN_KEYWORDS = ("frog", "toad", "salamander", "newt", "caecilian")

# ── Seasonal keywords: boost animals that are seasonally appropriate ──
_SEASONAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "spring": (
        "breeding", "nesting", "migration", "bloom", "chick", "fawn",
        "larva", "caterpillar", "butterfly", "tadpole", "gosling",
        "繁殖", "筑巢", "迁徙", "开花", "幼崽", "蝌蚪", "蝴蝶",
    ),
    "summer": (
        "active", "insect", "reptile", "bat", "moth", "frog", "cicada",
        "dragonfly", "lizard", "snake", "firefly", "cricket", "grasshopper",
        "昆虫", "蜥蜴", "蛇", "蝉", "蜻蜓", "蟋蟀", "萤火虫",
    ),
    "autumn": (
        "migration", "harvest", "mushroom", "berry", "hawk", "squirrel",
        "deer", "geese", "crane", "mushroom", "fungus",
        "迁徙", "蘑菇", "浆果", "鹰", "松鼠", "鹿", "大雁",
    ),
    "winter": (
        "dormant", "hibernate", "tracks", "frost", "owl", "fox", "hare",
        "wolf", "crow", "magpie", "evergreen", "conifer",
        "冬眠", "脚印", "霜", "猫头鹰", "狐狸", "野兔", "狼", "乌鸦", "喜鹊",
    ),
}

# ── Biome + season life matrix ─────────────────────────────────────
# (biome, season) → list of descriptive phrases for offline fallback
_BIOME_SEASON_LIFE: dict[tuple[str, str], list[str]] = {
    ("forest", "spring"): [
        "鹿带着幼崽在林间吃草", "啄木鸟在枯树上筑巢", "野花开了一地,蜜蜂在忙",
        "一只兔子蹲在灌木丛下,耳朵竖着",
    ],
    ("forest", "summer"): [
        "猫头鹰在树荫里闭着眼", "狐狸叼着猎物穿过小路", "萤火虫在暗处一闪一闪",
        "蝉叫得整个林子都在响", "蜻蜓停在水边的草叶上",
    ],
    ("forest", "autumn"): [
        "松鼠在埋橡果,刨了三个坑", "蘑菇从落叶里冒出来", "一群大雁往南飞",
        "鹿角挂在低矮的树枝上,是公鹿蹭掉的",
    ],
    ("forest", "winter"): [
        "雪地上有一串脚印,四趾的,是狐狸的", "树枝光秃秃的,像手指伸向天空",
        "一只乌鸦蹲在枯枝上,黑得发亮", "松鼠的爪印围着一棵松树绕了三圈",
    ],
    ("desert", "spring"): [
        "沙漠里开了一小片花,黄的", "蜥蜴趴在石头上晒太阳", "一群鸟往北飞",
    ],
    ("desert", "summer"): [
        "蝎子从石头底下钻出来", "蛇在沙地上留下蜿蜒的痕迹", "热气把远处的东西都扭曲了",
    ],
    ("desert", "autumn"): [
        "夜里凉了,沙狐出来觅食", "星星比任何地方都多", "蜥蜴钻进沙子里不见了",
    ],
    ("desert", "winter"): [
        "夜里冷得刺骨,霜落在沙面上", "沙狐的脚印在霜地上清晰可见", "一只猫头鹰蹲在仙人掌上",
    ],
    ("coast", "spring"): [
        "海鸥在沙滩上筑巢,叫声很吵", "螃蟹从洞里探出半个身子", "海藻被浪冲上来,湿漉漉的",
    ],
    ("coast", "summer"): [
        "海鸥在头顶盘旋", "螃蟹在礁石缝里横着走", "水母被冲到沙滩上,透明的",
        "海豚在远处的海面上跳",
    ],
    ("coast", "autumn"): [
        "海鸥往南飞,排成一排", "沙滩上有贝壳和海星的碎片", "螃蟹开始往深水里退",
    ],
    ("coast", "winter"): [
        "海鸥缩在礁石后面,羽毛被风吹乱", "沙滩上只有脚印,没有活物", "海浪把海草推到岸上,堆了一层",
    ],
    ("mountain", "spring"): [
        "雪线往上退了,露出草地", "岩羊在峭壁上吃草", "旱獭从洞里探出头来",
    ],
    ("mountain", "summer"): [
        "鹰在气流里盘旋", "旱獭站在岩石上叫", "高山草甸上野花铺了一片",
    ],
    ("mountain", "autumn"): [
        "岩羊往低处走", "风把草吹倒了一片", "鹰飞得很低,像在找什么",
    ],
    ("mountain", "winter"): [
        "雪地上有动物的脚印,通向远处", "旱獭冬眠了,洞口被雪封住", "一只雪豹的脚印,新的,你蹲下来看",
    ],
    ("grassland", "spring"): [
        "草刚冒头,嫩绿的", "兔子从草丛里窜出来", "一只鹰在高处悬停",
    ],
    ("grassland", "summer"): [
        "草长得比膝盖高,虫子在草尖上飞", "野兔蹲在草丛里,耳朵一动一动", "鹰在天上画圈",
    ],
    ("grassland", "autumn"): [
        "草黄了,风一吹像浪", "田鼠在囤粮食", "一群大雁飞过,叫声从远处传来",
    ],
    ("grassland", "winter"): [
        "草都枯了,地面上只有霜", "狐狸的脚印穿过空旷的草地", "一只鹰蹲在枯草上,缩着脖子",
    ],
    ("tundra", "spring"): [
        "地衣从融雪里露出来", "驯鹿往北走", "雪鹀在低矮的灌木上叫",
    ],
    ("tundra", "summer"): [
        "蚊子成群地飞", "驯鹿在苔原上吃草", "北极狐换了一身灰褐色的毛",
    ],
    ("tundra", "autumn"): [
        "驯鹿往南迁徙", "苔藓变成红色", "第一场雪来了",
    ],
    ("tundra", "winter"): [
        "雪地上什么都没有,白得刺眼", "北极狐的脚印在雪里", "一只雪鸮停在石头上,白色的",
    ],
    ("rainforest", "spring"): [
        "鸟叫声从四面八方来", "猴子在树冠里荡", "蝴蝶在溪边喝水",
    ],
    ("rainforest", "summer"): [
        "蝉叫得耳朵疼", "青蛙蹲在叶子上叫", "蛇从树枝上垂下来", "萤火虫在暗处一闪一闪",
    ],
    ("rainforest", "autumn"): [
        "果子熟了,猴子在摘", "蘑菇从腐木里冒出来", "蜂鸟在花前悬停",
    ],
    ("rainforest", "winter"): [
        "雨少了,溪水浅了", "蛇在石头上晒太阳", "鸟叫声少了,但还在",
    ],
}

# ── Biome ↔ animal filtering ──────────────────────────────────────
# Keywords that indicate an animal is alpine/temperate (should NOT appear in tropical/coast)
_ALPINE_KEYWORDS = (
    "marmot", "土拨鼠", "yak", "牦牛", "snow leopard", "雪豹",
    "pika", "鼠兔", "ibex", "山羊", "chamois", "岩羚羊",
    "mountain goat", "marmot", "alpine", "ptarmigan", "雷鸟",
    "wolverine", "狼獾", "ermine", "白鼬",
)
# Keywords that indicate an animal is tropical (should NOT appear in alpine/tundra)
_TROPICAL_KEYWORDS = (
    "parrot", "鹦鹉", "toucan", "巨嘴鸟", "monkey", "猴",
    "gorilla", "大猩猩", "chimpanzee", "黑猩猩", "orangutan", "猩猩",
    "jaguar", "美洲豹", "piranha", "食人鱼", "sloth", "树懒",
    "macaw", "金刚鹦鹉", "cobra", "眼镜蛇", "gecko", "壁虎",
    "iguana", "鬣蜥", "mango", "芒果", "hummingbird", "蜂鸟",
    "flamingo", "火烈鸟",
)
# Biome categories for filtering
_TROPICAL_BIOMES = frozenset({"coast", "rainforest", "island"})
_ALPINE_BIOMES = frozenset({"mountain", "tundra", "volcano"})


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    p = math.pi / 180
    a = (
        0.5
        - math.cos((lat2 - lat1) * p) / 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def _month_to_season(month: int | None) -> str:
    """Map month number (1-12) to season name."""
    if month is None:
        return ""
    return ["winter", "winter", "spring", "spring", "spring", "summer",
            "summer", "summer", "autumn", "autumn", "autumn", "winter"][month - 1]


async def nearby(
    lat: float,
    lon: float,
    *,
    night: bool,
    weather_text: str,
    radius_km: int = 10,
    biome: str | None = None,
    rng: random.Random | None = None,
    month: int | None = None,
) -> dict | None:
    """Return a nearby wildlife observation, or *None* if nothing found / API down.

    Parameters
    ----------
    night:
        If *True*, prefer nocturnal species.
    weather_text:
        Free-form weather string; if it contains rain-related characters,
        prefer amphibians.
    radius_km:
        Search radius;城市给小,荒野给大。
    biome:
        Current biome name (e.g. "mountain", "coast", "rainforest").
        Used to filter out biome-inappropriate animals.
    month:
        Month (1-12) for seasonal filtering. Animals that are seasonally
        appropriate get a score boost.
    """
    params = urlencode(
        {
            "lat": lat,
            "lng": lon,
            "radius": radius_km,
            "per_page": 20,
            "order": "desc",
            "order_by": "observed_on",
            "locale": "zh-CN",  # 俗名要中文的
        }
    )
    url = f"https://api.inaturalist.org/v1/observations?{params}"
    data = await providers.fetch_json(url, source=SOURCE, cache_ttl=300, timeout=5.0)
    if not data or not data.get("results"):
        return None

    results: list[dict] = data["results"]

    # Build scored list: (score, observation)
    rain = any(ch in weather_text for ch in ("雨", "rain", "雷", "storm"))
    season = _month_to_season(month)
    scored: list[tuple[float, dict]] = []
    for obs in results:
        taxon = obs.get("taxon") or {}
        name_lower = ((taxon.get("name") or "") + " " + (taxon.get("preferred_common_name") or "")).lower()
        score = 0.0
        if night:
            for kw in _NOCTURNAL_KEYWORDS:
                if kw in name_lower:
                    score += 2.0
                    break
        if rain:
            for kw in _AMPHIBIAN_KEYWORDS:
                if kw in name_lower:
                    score += 1.5
                    break
        # Seasonal boost: prefer animals that are seasonally active
        if season and season in _SEASONAL_KEYWORDS:
            for kw in _SEASONAL_KEYWORDS[season]:
                if kw in name_lower:
                    score += 1.0
                    break
        score += (rng or random).random()  # jitter
        scored.append((score, obs))

    scored.sort(key=lambda t: t[0], reverse=True)

    # Filter out biome-inappropriate animals
    if biome:
        is_tropical = biome in _TROPICAL_BIOMES
        is_alpine = biome in _ALPINE_BIOMES
        if is_tropical or is_alpine:
            filtered: list[tuple[float, dict]] = []
            for score, obs in scored:
                taxon = obs.get("taxon") or {}
                name_lower = ((taxon.get("name") or "") + " " + (taxon.get("preferred_common_name") or "")).lower()
                skip = False
                if is_tropical and any(kw in name_lower for kw in _ALPINE_KEYWORDS):
                    skip = True
                if is_alpine and any(kw in name_lower for kw in _TROPICAL_KEYWORDS):
                    skip = True
                if not skip:
                    filtered.append((score, obs))
            if filtered:
                scored = filtered

    best = scored[0][1]

    taxon = best.get("taxon") or {}
    geo = best.get("geojson") or {}
    coords = (geo.get("coordinates") or [None, None])
    obs_lon, obs_lat = coords[0], coords[1]

    dist_m: float | None = None
    if obs_lat is not None and obs_lon is not None:
        dist_m = round(_haversine_m(lat, lon, obs_lat, obs_lon))

    # Extract photo URL
    photos = best.get("photos") or best.get("observation_photos") or []
    photo_url = ""
    if photos:
        photo_url = photos[0].get("url", "")
        # iNaturalist returns sizes; prefer medium
        if photo_url:
            photo_url = photo_url.replace("square", "medium")

    iconic = (taxon.get("iconic_taxon_name") or "").lower()
    unit = "一棵" if iconic in ("plantae", "fungi") else "一只"

    return {
        "name": taxon.get("name", ""),
        "common_name": taxon.get("preferred_common_name", ""),
        "seen_at": best.get("observed_on", ""),
        "distance_m": dist_m,
        "photo_url": photo_url,
        "unit": unit,
        "season": season,
        "biome": biome or "",
    }
