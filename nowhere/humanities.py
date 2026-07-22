"""人文层一叠卡——作品/事件/人物足迹。

数据(humanities.json): 又又手写,旋复定声口。
规矩: 事实必须真,玩笑必须冷(一句封顶),事件层不幽默。

机制同方志(localcolor): 见过的不重复,抽完就没了。
展开顺序: 先事(事件)、再人(人物)、后作品——
这地方先是真的,然后有人来过,然后被写进书里。
"""

from __future__ import annotations

import json
import math
import pathlib
import random

_DATA = pathlib.Path(__file__).resolve().parent / "data" / "humanities.json"

_raw: dict | None = None
_places: dict | None = None
_aliases: dict | None = None


def _load() -> dict:
    global _raw, _places, _aliases
    if _raw is None:
        _raw = json.loads(_DATA.read_text(encoding="utf-8")) if _DATA.exists() else {}
        _places = _raw.get("places", {})
        _aliases = _raw.get("aliases", {})
    return _raw


def _haversine_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Haversine distance in km."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a_lat, a_lon, b_lat, b_lon))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _resolve(place_name: str | None) -> str | None:
    """地名过别名表——地理编码返回什么名字都能挂上。"""
    if not place_name:
        return None
    _load()
    return _aliases.get(place_name, place_name)


def has_place(place_name: str | None) -> bool:
    """此地有人文卡就算有。"""
    name = _resolve(place_name)
    return bool(name and name in _places)


def get_place_coords(place_name: str) -> dict | None:
    """Look up a place by name, return {"lat", "lon"} or None."""
    _load()
    entry = _places.get(place_name)
    if entry and "lat" in entry and "lon" in entry:
        return {"lat": entry["lat"], "lon": entry["lon"]}
    # Try aliases
    alias = _aliases.get(place_name)
    if alias:
        entry = _places.get(alias)
        if entry and "lat" in entry and "lon" in entry:
            return {"lat": entry["lat"], "lon": entry["lon"]}
    return None


def draw(place_name: str | None, seen: set[str], rng: random.Random) -> dict | None:
    """抽一张没见过的卡 {"category", "text", "key", "ref"};抽完或无此地 → None。

    优先级: 事件 → 人物 → 作品。同一类里随机。
    ref 带 name/title/creator/kind——追问走 ask(ZIM) 时用。
    """
    name = _resolve(place_name)
    if not name:
        return None
    entry = _load().get("places", {}).get(name)
    if not entry:
        return None

    for cat in ("事件", "人物", "作品"):
        cards = entry.get(cat, [])
        unseen = [
            (i, c) for i, c in enumerate(cards) if f"{name}/{cat}/{i}" not in seen
        ]
        if not unseen:
            continue
        i, card = rng.choice(unseen)
        ref = {k: v for k, v in card.items() if k != "text"}
        return {
            "category": cat,
            "text": card["text"],
            "key": f"{name}/{cat}/{i}",
            "ref": ref,
        }
    return None


def nearby_place(
    lat: float,
    lon: float,
    seen: set[str],
    rng: random.Random,
    radius_km: float = 5.0,
    destination: str | None = None,
) -> dict | None:
    """Walk 到附近时触发人文卡。

    返回 {"place", "category", "text", "key", "ref"} 或 None。
    优先级: 目的地 > 距离最近 > 事件 > 人物 > 作品。
    """
    _load()
    assert _places is not None

    # 收集范围内的地名(带距离)
    candidates: list[tuple[str, float]] = []
    for name, entry in _places.items():
        elat = entry.get("lat")
        elon = entry.get("lon")
        if elat is None or elon is None:
            continue
        dist = _haversine_km(lat, lon, elat, elon)
        if dist <= radius_km:
            candidates.append((name, dist))

    if not candidates:
        return None

    # 目的地解析
    dest_resolved = _resolve(destination) if destination else None

    # 只留有未见卡的
    def _has_unseen(name: str) -> bool:
        entry = _places.get(name, {})
        for cat in ("事件", "人物", "作品"):
            cards = entry.get(cat, [])
            for i in range(len(cards)):
                if f"{name}/{cat}/{i}" not in seen:
                    return True
        return False

    candidates = [(n, d) for n, d in candidates if _has_unseen(n)]
    if not candidates:
        return None

    # 排序: 目的地排最前,然后按距离
    candidates.sort(key=lambda x: (
        0 if x[0] == dest_resolved else 1,
        x[1],
    ))

    place_name = candidates[0][0]
    card = draw(place_name, seen, rng)
    if card is None:
        return None
    return {
        "place": place_name,
        "category": card["category"],
        "text": card["text"],
        "key": card["key"],
        "ref": card.get("ref", {}),
    }
