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
import sys

from nowhere import cards as _cards

_DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
_DATA = _DATA_DIR / "humanities.json"
_REGIONAL_FILES = [
    "humanities_films.json",
    "humanities_historical.json",
]

# ── Card 53: 重地列表——屠杀/灾难/战争遗址 ─────────────────────────────
# weight="heavy" 的地名, salience 用此做重力维度。
_HEAVY_EVENT_NAMES: set[str] = {
    "卡廷惨案", "南京大屠杀", "广岛原爆", "奥斯维辛",
    "卢旺达种族灭绝", "亚美尼亚大屠杀", "格尔尼卡轰炸", "索姆河战役",
}

_raw: dict | None = None
_places: dict | None = None
_aliases: dict | None = None
_hu_cards: list[_cards.Card] | None = None


def _load() -> dict:
    """Load raw humanities JSON (for coords/aliases).  Returns full raw dict."""
    global _raw, _places, _aliases
    if _raw is not None:
        return _raw

    _raw = json.loads(_DATA.read_text(encoding="utf-8")) if _DATA.exists() else {}
    _places = _raw.get("places", {})
    _aliases = _raw.get("aliases", {})
    main_count = len(_places)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"[humanities] main: {main_count} places", flush=True)

    for fname in _REGIONAL_FILES:
        p = _DATA_DIR / fname
        if not p.exists():
            continue
        regional = json.loads(p.read_text(encoding="utf-8"))
        # regional files may be flat {place: data} or nested {places: {...}}
        if "places" in regional and isinstance(regional["places"], dict):
            entries = regional["places"]
        else:
            entries = regional
        added = 0
        for k, v in entries.items():
            if k.startswith("_"):
                continue  # skip metadata keys like _说明
            if k not in _places:
                _places[k] = v
                added += 1
        print(f"[humanities] {fname}: {len(entries)} total, {added} new merged", flush=True)

    # ── Card 53: stamp weight on places with heavy events ──────────────
    for _pname, _pentry in _places.items():
        if not isinstance(_pentry, dict):
            continue
        for _cat in ("事件",):
            for _card in _pentry.get(_cat, []):
                if _card.get("name") in _HEAVY_EVENT_NAMES:
                    _pentry["weight"] = "heavy"
                    break
            if _pentry.get("weight") == "heavy":
                break

    print(f"[humanities] merged total: {len(_places)} places", flush=True)
    return _raw


def is_heavy_place(place_name: str | None) -> bool:
    """Card 53: 此地是否重地(屠杀/灾难/战争遗址)。"""
    if not place_name:
        return False
    _load()
    entry = _places.get(place_name)
    if not entry:
        return False
    return entry.get("weight") == "heavy"


def get_place_weight(place_name: str | None) -> str:
    """Card 53: 返回地名的 weight 等级。'heavy' 或 'normal'。"""
    if not place_name:
        return "normal"
    _load()
    entry = _places.get(place_name)
    if not entry:
        return "normal"
    return entry.get("weight", "normal")


def _get_cards() -> list[_cards.Card]:
    """Load humanities cards via the unified Card layer."""
    global _hu_cards
    if _hu_cards is not None:
        return _hu_cards
    _hu_cards = _cards.load_humanities(_DATA_DIR)
    return _hu_cards


def _haversine_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Haversine distance in km."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a_lat, a_lon, b_lat, b_lon))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    a = min(a, 1.0)
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
    if not name:
        return False
    return any(c.conditions.get("place") == name for c in _get_cards())


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

    for cat in ("事件", "人物", "作品"):
        unseen = [
            c for c in _get_cards()
            if c.conditions.get("place") == name
            and c.meta.get("category") == cat
            and c.id not in seen
        ]
        if not unseen:
            continue
        card = rng.choice(unseen)
        # Build ref from meta (all fields except category)
        ref = {k: v for k, v in card.meta.items() if k != "category"}
        return {
            "category": cat,
            "text": card.text,
            "key": card.id,
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
        return any(
            c.conditions.get("place") == name and c.id not in seen
            for c in _get_cards()
        )

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
