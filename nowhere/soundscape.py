"""声景——环境的声音,全部由真实数据推出,永远离线。

规则: 每个声音必须指得回数据字段。风=wind_ms,雨=precip,叶响=forest,
浪=水面/离岸,底噪=urban,虫鸣=夜晚+温暖+植被。编的一律不写。
"""

from __future__ import annotations

import json
import math
import pathlib
import random
from typing import Final

_DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"

_FALLBACK_PATH: Final = _DATA_DIR / "radio_fallback.json"
_EARTH_RADIUS_KM: Final = 6371.0

# 统一引用 describe 的权威定义，消除重复映射
from nowhere.describe import _SURFACE_ZH

_SURFACE_SOUND: dict[str, str] = {
    "forest": "树叶子哗哗地响",
    "grass": "草叶一阵一阵地伏下去",
    "rock": "石缝呜呜地叫",
    "bare": "碎石缝呜呜地叫",
    "snow": "雪面上什么声音都站不住",
    "ice": "冰面偶尔咔的一声,远远的",
    "sand": "沙被吹得贴着地皮走",
    "urban": "车声远远地滚,人声的底噪",
    "wetland": "水草叶子互相擦",
}


def describe_sound(env: dict, rng: random.Random) -> str:
    """输入环境快照 {"weather","surface","sky","mode"},输出一段声景散文。

    全安静也输出——静也是一种声景(辽阔和孤独也是信息)。
    """
    weather = env.get("weather") or {}
    sky = env.get("sky") or {}
    surface = env.get("surface", "")
    mode = env.get("mode", "land")

    wind = weather.get("wind_ms", 0)
    precip = weather.get("precip", "none")
    temp = weather.get("temp_c", 15)
    night = sky.get("phase") in ("night", "nautical")

    sounds: list[str] = []

    # 降水压过一切
    if precip == "rain":
        target = _SURFACE_SOUND.get(surface, "地")
        sounds.append(rng.choice([
            "雨声。雨点砸下来,把别的声音都盖住了。",
            f"雨一阵密一阵疏,落在{_SURFACE_ZH.get(surface, '地面')}上。",
            "雨。世界只剩这一种声音。",
        ]))
        return "".join(sounds)
    if precip == "snow":
        return rng.choice([
            "雪把声音都吃掉了。静。",
            "落雪无声。连自己呼吸都听得见。",
        ])

    # 风
    if wind >= 12:
        sounds.append(rng.choice([
            "风在吼,一阵紧过一阵。",
            "风声大,说话得贴着耳朵喊。",
        ]))
    elif wind >= 6:
        detail = _SURFACE_SOUND.get(surface)
        if detail:
            sounds.append(f"风起来了,{detail}。")
        else:
            sounds.append("风一阵一阵。")
    elif wind >= 2:
        sounds.append("风小,一阵一阵。")

    # 水
    if mode == "water" or surface in ("water_ocean",):
        sounds.append(rng.choice([
            "浪一下一下,把人托起来又放下去。",
            "水声就在耳边,一下一下。",
        ]))
    elif surface == "water_fresh":
        sounds.append("水声细,一下一下拍着岸。")

    # 城市底噪
    if surface == "urban" and wind < 6:
        sounds.append(_SURFACE_SOUND["urban"] + "。")

    # 夜+暖+植被 → 虫
    if night and temp > 15 and surface in ("forest", "grass", "wetland"):
        sounds.append("虫声一层一层的,不知疲倦。")

    if not sounds:
        return rng.choice([
            "四下无人。风是这里唯一的声音。",
            "静。静得能听见自己的心跳。",
            "什么声音也没有。世界好像只剩你一个。",
        ])

    return "".join(sounds)


# ── Card 19: Dawn Chorus (日出前鸟叫) ──────────────────────────────

_dawn_chorus_cache: dict | None = None


def _load_dawn_chorus() -> dict:
    global _dawn_chorus_cache
    if _dawn_chorus_cache is None:
        fp = _DATA_DIR / "dawn_chorus.json"
        if fp.exists():
            _dawn_chorus_cache = json.loads(fp.read_text(encoding="utf-8"))
        else:
            _dawn_chorus_cache = {}
    return _dawn_chorus_cache


def dawn_chorus(biome: str, sun_alt: float, rng: random.Random) -> str | None:
    """Return a dawn chorus card if sun_alt is in -6..0 window.

    Intensity maps to sun_alt position: -6→first(一只), -0→last(满).
    Biome picks forest/city/water group; fallback to city.
    """
    if sun_alt < -6.0 or sun_alt > 0.0:
        return None
    data = _load_dawn_chorus()
    # pick biome group
    group_key = biome if biome in data else "city"
    pool = data.get(group_key, data.get("city", []))
    if not pool:
        return None
    # map sun_alt to index: -6→0, -0→3
    t = (sun_alt + 6.0) / 6.0  # 0..1
    idx = min(3, int(t * 4))
    return pool[idx]


# ── Card 21: Soundscape Credits (声音出处) ──────────────────────────

_credits_cache: dict | None = None


def _load_credits() -> dict:
    global _credits_cache
    if _credits_cache is None:
        fp = _DATA_DIR / "soundscape_credits.json"
        if fp.exists():
            _credits_cache = json.loads(fp.read_text(encoding="utf-8"))
        else:
            _credits_cache = {}
    return _credits_cache


# biome → credits key mapping
_BIOME_CREDIT_MAP: dict[str, str] = {
    "forest": "forest", "rainforest": "forest",
    "city": "urban", "urban": "urban",
    "coast": "water_ocean", "water_ocean": "water_ocean",
    "desert": "desert", "sand": "desert",
    "grassland": "grass", "grass": "grass",
    "tundra": "tundra", "snow": "tundra",
    "mountain": "mountain", "rock": "mountain",
    "wetland": "wetland",
}


def soundscape_credit(
    biome: str,
    rng: random.Random,
    listener_lat: float = 0.0,
    listener_lon: float = 0.0,
) -> str | None:
    """20% chance to return a soundscape credit line matching biome.

    Card 68: credits with lat/lon are distance-filtered — if the recording
    location is >200 km from the listener, skip it.  Local silence beats
    distant sound (重庆解放碑 should not appear in 拉萨).

    Returns None if no match, roll fails, or distance too great.
    """
    if rng.random() > 0.20:
        return None
    credits_data = _load_credits()
    credit_key = _BIOME_CREDIT_MAP.get(biome, "")
    pool = credits_data.get(credit_key, [])
    if not pool:
        return None

    # Distance filter: exclude entries >200 km from listener
    if listener_lat != 0.0 or listener_lon != 0.0:
        near_pool = []
        for entry in pool:
            elat = entry.get("lat")
            elon = entry.get("lon")
            if elat is None or elon is None:
                # No coords (ocean/international): always eligible
                near_pool.append(entry)
            elif _haversine_km(listener_lat, listener_lon, elat, elon) <= 200:
                near_pool.append(entry)
        if not pool:
            # No entries at all — silent
            return None
        if near_pool:
            pool = near_pool
        else:
            # All entries are >200 km — return silent (no distant sound)
            return None

    entry = rng.choice(pool)
    who = entry.get("who", "")
    where = entry.get("where", "")
    note = entry.get("note", "")
    # 3 text templates
    templates = [
        f"这段声音,是{who}在{where}录的。{note}",
        f"你听到的这些,是{who}在{where}收来的。{note}",
        f"耳机里这些,来自{where},{who}录的。{note}",
    ]
    return rng.choice(templates)


# ── Card 22: Radio Station Selection (选台国家码阈值) ──────────────

_radio_cache: list[dict] | None = None
# Pre-grouped live stations: country_code → [station, ...]
_radio_by_country: dict[str, list[dict]] | None = None
_radio_live_all: list[dict] | None = None


def _load_radio_fallback() -> list[dict]:
    global _radio_cache
    if _radio_cache is None:
        if _FALLBACK_PATH.exists():
            _radio_cache = json.loads(_FALLBACK_PATH.read_text(encoding="utf-8"))
        else:
            _radio_cache = []
    return _radio_cache


def _ensure_radio_index() -> tuple[dict[str, list[dict]], list[dict]]:
    """Build (once) a country→stations index and a flat live-stations list.

    Returns ``(by_country, live_all)``.  Both are cached at module level so
    ``select_station()`` can skip the three list-comprehension scans.
    """
    global _radio_by_country, _radio_live_all
    if _radio_by_country is not None:
        return _radio_by_country, _radio_live_all  # type: ignore[return-value]

    stations = _load_radio_fallback()
    by_country: dict[str, list[dict]] = {}
    live_all: list[dict] = []
    for st in stations:
        if st.get("dead"):
            continue
        live_all.append(st)
        cc = st.get("country", "")
        by_country.setdefault(cc, []).append(st)

    _radio_by_country = by_country
    _radio_live_all = live_all
    return by_country, live_all


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


_CULTURE_CIRCLES: dict[str, list[str]] = {
    "arabic":    ["EG", "LY", "TN", "DZ", "MA", "SA", "AE", "JO", "IQ", "SY", "LB", "YE", "OM", "QA", "KW", "BH", "SD"],
    "turkic":    ["TR", "KG"],
    "persian":   ["IR"],
    "south_asia":["IN", "PK", "BD"],
    "east_asia": ["CN", "KR", "JP", "VN", "TH", "ID", "MY", "PH", "KH", "MM"],
    "europe":    ["GB", "FR", "DE", "NO", "IS", "CZ", "IT", "UA"],
    "americas":  ["US", "CA", "BR", "AR", "PE", "CO", "CL", "MX", "BO"],
    "africa":    ["KE", "TZ", "ZA", "CM", "ET", "GH", "NG"],
    "oceania":   ["AU", "NZ", "FJ"],
}

_MAX_NEARBY_KM: float = 3000.0


def select_station(
    lat: float,
    lon: float,
    country_code: str,
    rng: random.Random | None = None,
) -> dict | None:
    """Select a radio station for the given location.

    Card 68 fallback chain:
      1. Same-country (nearest by haversine)
      2. Same culture circle (nearest)
      3. Nearby (haversine ≤ 3000 km, any station)
      4. None (caller should render a "quiet" variant)

    Station entries with ``"dead": true`` are skipped entirely.

    Returns a station dict ``{name, genre, stream_url, homepage, country}``
    or *None* if no station is available.
    """
    by_country, live = _ensure_radio_index()
    if not live:
        return None

    def _pick_nearest(pool: list[dict]) -> dict | None:
        if not pool:
            return None
        best = None
        best_dist = math.inf
        for st in pool:
            st_lat = st.get("lat")
            st_lon = st.get("lon")
            if st_lat is None or st_lon is None:
                continue
            d = _haversine_km(lat, lon, st_lat, st_lon)
            if d < best_dist:
                best_dist = d
                best = st
        return best

    # 1. Same-country nearest (O(1) dict lookup instead of full scan)
    pick = _pick_nearest(by_country.get(country_code, []))
    if pick is not None:
        return pick

    # 2. Same culture circle
    circle_ccs: list[str] = []
    for _name, ccs in _CULTURE_CIRCLES.items():
        if country_code in ccs:
            circle_ccs = ccs
            break
    if circle_ccs:
        circle_pool: list[dict] = []
        for cc in circle_ccs:
            circle_pool.extend(by_country.get(cc, []))
        pick = _pick_nearest(circle_pool)
        if pick is not None:
            return pick

    # 3. Nearby by haversine ≤ 3000 km, excluding politically sensitive pairs
    _EXCLUDE: dict[str, set[str]] = {
        "RU": {"UA"}, "UA": {"RU"},
        "CN": {"TW"}, "TW": {"CN"},
        "KR": {"KP"}, "KP": {"KR"},
        "IL": {"PS"}, "PS": {"IL"},
        "MV": {"IN"},
    }
    excluded = _EXCLUDE.get(country_code, set())
    best: dict | None = None
    best_dist = math.inf
    for st in live:
        st_cc = st.get("country", "")
        if st_cc in excluded:
            continue
        st_lat = st.get("lat")
        st_lon = st.get("lon")
        if st_lat is None or st_lon is None:
            continue
        d = _haversine_km(lat, lon, st_lat, st_lon)
        if d < best_dist:
            best_dist = d
            best = st
    if best is not None and best_dist <= _MAX_NEARBY_KM:
        return best

    # 4. No station available (silent)
    return None


def radio_quiet_text(rng: random.Random) -> str:
    """Return a "quiet radio" variant when no station is available."""
    return rng.choice([
        "收音机搜了一圈,只有沙沙的白噪音。",
        "旋钮转到底,什么也没收到。只有电流的嘶嘶声。",
        "电台一个都没收到。安静。",
        "调频里空空的,偶尔一声咔嗒。",
    ])
